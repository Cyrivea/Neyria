"""Kunbot 图像 Provider 测试：URL 与 b64 双路转存、尺寸映射、未配置报错（E2）。"""

import base64

import pytest

import services.capabilities.kunbot_image as ki
from schemas.capability import ImageGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError


def _inputs(**kw):
    data = {"prompt": "海边黄昏", "style": "电影概念艺术", "ratio": "1:1", "palette": "深蓝与紫色"}
    data.update(kw)
    return ImageGenerateInput(**data)


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(ki.config, "KUNBOT_API_KEY", "")
    with pytest.raises(ModelNotConfiguredError):
        ki.generate_image(_inputs())


def test_url_response_is_downloaded_and_rehosted(monkeypatch, tmp_path):
    monkeypatch.setattr(ki.config, "KUNBOT_API_KEY", "sk-test")
    monkeypatch.setattr(ki.config, "KUNBOT_IMAGE_BASE", "http://gw.test/v1")
    monkeypatch.setattr(ki.config, "IMAGE_MODEL_KUNBOT", "gpt-image-2")
    monkeypatch.setattr(ki.config, "DOCUMENT_STORAGE_DIR", tmp_path)

    calls = []

    class _Post:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"url": "https://temp.example/x.png?sig=1"}]}

    class _Get:
        status_code = 200
        content = b"\x89PNG fake"

        def raise_for_status(self):
            return None

    def fake_post(url, **kw):
        calls.append(("POST", url, kw))
        assert url == "http://gw.test/v1/images/generations"
        return _Post()

    def fake_get(url, **kw):
        calls.append(("GET", url, kw))
        return _Get()

    monkeypatch.setattr(ki.httpx, "post", fake_post)
    monkeypatch.setattr(ki.httpx, "get", fake_get)

    result = ki.generate_image(_inputs())

    assert result["asset_path"].startswith("/api/assets/")
    assert result["asset"]["capability_id"] == "image.generate"
    assert "![曲绘]" in result["content"]
    assert "海边黄昏" in calls[0][2]["json"]["prompt"]
    assert calls[0][2]["json"]["size"] == "1024x1024"
    assert calls[1][0] == "GET"
    assert "temp.example" in calls[1][1]


def test_b64_json_response_lands_on_disk_without_download(monkeypatch, tmp_path):
    monkeypatch.setattr(ki.config, "KUNBOT_API_KEY", "sk-test")
    monkeypatch.setattr(ki.config, "DOCUMENT_STORAGE_DIR", tmp_path)

    payload = base64.b64encode(b"\x89PNG fake").decode()

    class _Post:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": payload}]}

    def fake_get(url, **kw):  # pragma: no cover - b64 路径绝不能有出网下载
        raise AssertionError("b64 路径不该再下载")

    monkeypatch.setattr(ki.httpx, "post", lambda *a, **kw: _Post())
    monkeypatch.setattr(ki.httpx, "get", fake_get)

    result = ki.generate_image(_inputs(ratio="16:9"))
    fname = result["asset"]["filename"]
    disk = (tmp_path.parent / "assets" / fname)
    assert disk.read_bytes() == b"\x89PNG fake"


def test_http_error_is_runtime_message_not_leaking_upstream(monkeypatch, tmp_path):
    monkeypatch.setattr(ki.config, "KUNBOT_API_KEY", "sk-test")
    monkeypatch.setattr(ki.config, "DOCUMENT_STORAGE_DIR", tmp_path)

    import httpx as _httpx

    class _Post402:
        status_code = 402

        def raise_for_status(self):
            req = _httpx.Request("POST", "http://gw.test/v1/images/generations")
            resp = _httpx.Response(402, request=req)
            raise _httpx.HTTPStatusError("402", request=req, response=resp)

    monkeypatch.setattr(ki.httpx, "post", lambda *a, **kw: _Post402())

    with pytest.raises(RuntimeError) as exc:
        ki.generate_image(_inputs())
    assert "HTTP 402" in str(exc.value)
