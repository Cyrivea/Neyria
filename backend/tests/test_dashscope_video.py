"""百炼（DashScope）视频 Provider：提交→轮询→转存全链，
usage 如实按「最终拼出的真实秒数」上报（按秒计费，连提交参数报什么都不能等额）。
"""

import pytest

import services.capabilities.dashscope_video as dv
from core import config
from schemas.capability import VideoGenerateInput
from services.capabilities.zhipu_text import ModelNotConfiguredError


class _F:
    def __init__(self, *, json_body=None, content=b"", status=200):
        self._j = json_body or {}
        self.content = content
        self.status_code = status

    def json(self):
        return self._j

    def raise_for_status(self):
        if self.status_code != 200:
            raise AssertionError(f"HTTP {self.status_code}")


def _inputs(**kw):
    data = {"prompt": "圆月下的湖面", "style": "电影写实", "ratio": "16:9", "duration": "5 秒"}
    data.update(kw)
    return VideoGenerateInput(**data)


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    with pytest.raises(ModelNotConfiguredError):
        dv.generate_video(_inputs())


def test_long_duration_rejected_honestly(monkeypatch):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
    with pytest.raises(ModelNotConfiguredError, match="仅产 5 秒"):
        dv.generate_video(_inputs(duration="10 秒"))


def test_full_loop_submit_poll_download_and_usage_counts_seconds(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(config, "DASHSCOPE_TASK_BASE", "http://bailian.test")
    monkeypatch.setattr(config, "VIDEO_MODEL_DASH", "wanx2.1-t2v-turbo")
    monkeypatch.setattr(config, "DOCUMENT_STORAGE_DIR", tmp_path)

    calls: list[tuple[str, str]] = []

    def fake_post(url, headers=None, json=None, timeout=None, files=None, data=None, **kw):
        calls.append(("POST", url))
        assert url == "http://bailian.test/services/aigc/video-generation/video-synthesis"
        assert headers.get("Authorization") == "Bearer sk-test"
        assert headers.get("X-DashScope-Async") == "enable"
        assert json["model"] == "wanx2.1-t2v-turbo"
        assert json["parameters"]["size"] == "1280*720"
        assert json["parameters"]["prompt_extend"] is False
        return _F(json_body={"output": {"task_id": "task-9"}})

    poll_calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None, follow_redirects=None, **kw):
        calls.append(("GET", url))
        if "tasks/task-9" in url:
            poll_calls["n"] += 1
            if poll_calls["n"] == 1:
                return _F(json_body={"output": {"task_status": "PENDING"}})
            return _F(json_body={
                "output": {
                    "task_status": "SUCCEEDED",
                    "video_url": "http://bailian.test/fake-cdn/v.mp4",
                    "usage": {"video_duration": 5},
                }
            })
        assert url.startswith("http://bailian.test")
        return _F(content=b"\x00\x00\x00\x00ftyp dream mp4")

    monkeypatch.setattr(dv.httpx, "post", fake_post)
    monkeypatch.setattr(dv.httpx, "get", fake_get)
    monkeypatch.setattr(dv.time, "sleep", lambda s: None)

    out = dv.generate_video(_inputs())
    assert out["asset"]["filename"].endswith(".mp4")
    assert out["asset"]["byte_size"] > 0
    # 账务：秒数存放在 completion_tokens/total_tokens，上游是「按秒计费」的数学实在
    assert out["usage"]["completion_tokens"] == 5
    assert out["usage"]["model"] == "wanx2.1-t2v-turbo"  # model 列只能是纯型号（成本表 Lookup Key）


def test_terminal_failure_raises_with_message(monkeypatch):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(config, "DASHSCOPE_TASK_BASE", "http://bailian.test")

    def fake_post(url, **kw):
        return _F(json_body={"output": {"task_id": "t"}})

    def fake_get(url, **kw):
        return _F(json_body={
            "output": {"task_status": "FAILED", "code": "DataInspection", "message": "内容审核未通过"}
        })

    monkeypatch.setattr(dv.httpx, "post", fake_post)
    monkeypatch.setattr(dv.httpx, "get", fake_get)
    monkeypatch.setattr(dv.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="内容审核未通过"):
        dv.generate_video(_inputs())


def test_timeout_raises(monkeypatch):
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(config, "DASHSCOPE_TASK_BASE", "http://bailian.test")
    monkeypatch.setattr(dv, "_POLL_TIMEOUT_SECONDS", 0)  # 立即逾期

    monkeypatch.setattr(dv.httpx, "post", lambda *a, **kw: _F(json_body={"output": {"task_id": "t"}}))
    monkeypatch.setattr(dv.httpx, "get", lambda *a, **kw: _F(json_body={"output": {"task_status": "PENDING"}}))
    with pytest.raises(RuntimeError, match="超时"):
        dv.generate_video(_inputs())


# ---------- embedding 备胎 ----------

def test_embedding_dashscope_parses_v4_shape(monkeypatch):
    from services.rag import embedding_dashscope as ed

    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "sk-emb")

    def fake_post(url, headers=None, json=None, **kw):
        assert json["model"] == "text-embedding-v4"
        assert json["input"]["texts"] == [" hello ", "world"]
        return _F(json_body={"output": {"embeddings": [
            {"text_index": 1, "embedding": [0.2]},
            {"text_index": 0, "embedding": [0.1]},  # 乱序返回，要保证按 text_index 还原
        ]}})

    monkeypatch.setattr(ed.httpx, "post", fake_post)
    out = ed.get_embeddings_batch([" hello ", "world"])
    assert out == [[0.1], [0.2]]
