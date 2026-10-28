"""ComfyUI 本地视频 Provider 测试：提交→轮询→下载转存全链 + 参数原位打参。"""

import pytest

import services.capabilities.comfyui_video as cv
from schemas.capability import VideoGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError


def _inputs(**kw):
    data = {"prompt": "落日下的湖面，镜头缓缓推近", "style": "电影写实", "ratio": "16:9", "duration": "5 秒"}
    data.update(kw)
    return VideoGenerateInput(**data)


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(cv.config, "COMFYUI_BASE_URL", "")
    with pytest.raises(ModelNotConfiguredError):
        cv.generate_video(_inputs())


class _Post:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"prompt_id": "pid-123"}


class _GetHistoryPending:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {}  # 还在排队


class _GetView:
    status_code = 200
    content = b"fake-mp4-bytes"

    def raise_for_status(self):
        return None


def _history_done(filename: str = "neyria/abc123.mp4") -> type:
    class _H:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "pid-123": {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {
                        "14": {
                            "videos": [
                                {"filename": filename, "subfolder": "video", "type": "output"}
                            ]
                        }
                    },
                }
            }

    return _H


def _patch_transport(monkeypatch, tmp_path, history_cls, captured: dict | None = None):
    """替掉 httpx.post / httpx.get，让 storage 撞上 tmp_path，并顺带抓提交物。"""
    monkeypatch.setattr(cv.config, "COMFYUI_BASE_URL", "http://comfy.test:8188")
    monkeypatch.setattr(cv.config, "DOCUMENT_STORAGE_DIR", tmp_path)

    state = {"polls": 0}

    def fake_post(url, **kw):
        assert url == "http://comfy.test:8188/prompt"
        if captured is not None:
            captured["workflow"] = kw["json"]["prompt"]
        return _Post()

    def fake_get(url, **kw):
        if url.endswith("/history/pid-123"):
            state["polls"] += 1
            # 第一次查排队，第二次查完成——走通 pending 分支
            return _GetHistoryPending() if state["polls"] == 1 else history_cls()
        if url.endswith("/view"):
            return _GetView()
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(cv.httpx, "post", fake_post)
    monkeypatch.setattr(cv.httpx, "get", fake_get)
    monkeypatch.setattr(cv.time, "sleep", lambda s: None)
    return state


def test_happy_path_submits_polls_and_rehosts(monkeypatch, tmp_path):
    captured: dict = {}
    _patch_transport(monkeypatch, tmp_path, _history_done(), captured)

    out = cv.generate_video(_inputs())

    # 提示词与风格原位打进节点 4；尺寸/帧数进节点 6；种子随机且进节点 7
    wf = captured["workflow"]
    assert "落日下的湖面" in wf["4"]["inputs"]["text"]
    assert "电影写实" in wf["4"]["inputs"]["text"]
    assert wf["6"]["inputs"]["width"] == 768 and wf["6"]["inputs"]["height"] == 512
    assert wf["6"]["inputs"]["length"] == 121
    assert isinstance(wf["7"]["inputs"]["noise_seed"], int)
    assert wf["14"]["inputs"]["filename_prefix"].startswith("neyria/")

    # 资产已落盘、记到 usage 里的模型是 ComfyUI 本地那台
    assert out["asset"]["byte_size"] == len(b"fake-mp4-bytes")
    assert (tmp_path.parent / "assets" / out["asset"]["filename"]).exists()
    assert out["usage"]["model"] == cv.config.VIDEO_MODEL_COMFYUI


def test_duration_and_ratio_tables(monkeypatch, tmp_path):
    captured: dict = {}
    _patch_transport(monkeypatch, tmp_path, _history_done(), captured)
    cv.generate_video(_inputs(ratio="9:16", duration="10 秒"))
    wf = captured["workflow"]
    assert wf["6"]["inputs"]["width"] == 512 and wf["6"]["inputs"]["height"] == 768
    assert wf["6"]["inputs"]["length"] == 241  # 8n+1


def test_error_status_raises_with_reason(monkeypatch, tmp_path):
    class _HErr(_history_done()):
        def json(self):
            return {
                "pid-123": {
                    "status": {
                        "status_str": "error",
                        "completed": False,
                        "messages": [["error", {"exception_message": "CUDA out of memory"}]],
                    },
                    "outputs": {},
                }
            }

    _patch_transport(monkeypatch, tmp_path, _HErr)
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        cv.generate_video(_inputs())


def test_no_video_output_raises(monkeypatch, tmp_path):
    class _HNoFile(_history_done()):
        def json(self):
            return {
                "pid-123": {
                    "status": {"status_str": "success", "completed": True, "messages": []},
                    "outputs": {"14": {"videos": [{"filename": "a.png"}]}},
                }
            }

    _patch_transport(monkeypatch, tmp_path, _HNoFile)
    with pytest.raises(RuntimeError, match="没有找到视频文件"):
        cv.generate_video(_inputs())


def test_timeout_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(cv.config, "COMFYUI_BASE_URL", "http://comfy.test:8188")
    monkeypatch.setattr(cv.config, "DOCUMENT_STORAGE_DIR", tmp_path)
    monkeypatch.setattr(cv.config, "COMFYUI_TIMEOUT_SECONDS", 0)  # 立即超时

    monkeypatch.setattr(cv.httpx, "post", lambda url, **kw: _Post())
    monkeypatch.setattr(cv.httpx, "get", lambda url, **kw: _GetHistoryPending())

    with pytest.raises(RuntimeError, match="超时"):
        cv.generate_video(_inputs())


def test_dispatch_by_provider(monkeypatch):
    """runtime 分流：VIDEO_PROVIDER=comfyui 时不该再进 siliconflow 模块。"""
    import services.capabilities.runtime as rt

    monkeypatch.setattr(rt.config, "VIDEO_PROVIDER", "comfyui")
    assert rt._video_billing_model() == rt.config.VIDEO_MODEL_COMFYUI
    monkeypatch.setattr(rt.config, "VIDEO_PROVIDER", "siliconflow")
    assert rt._video_billing_model() == rt.config.VIDEO_MODEL
