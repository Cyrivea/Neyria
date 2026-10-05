"""C12/G5-②：LLM 抽象层的行为锁定（不连真实上游）。

- 适配器把中立类型翻译进/出 SDK 形状；
- 工厂的 provider 选择、缺密钥降级、未知 provider 启动即炸；
- 超时必须显式传给 SDK（构造 + 每次请求）。
"""

from __future__ import annotations

import pytest

from services.llm import get_chat_client, reset_chat_client
from services.llm.zhipu import ZhipuChatClient


class _SDKSpy:
    """替 ZhipuChatClient 体内的 SDK：recording 构造超时与每次请求 kwargs。"""

    def __init__(self, **ctor_kwargs):
        self.ctor_kwargs = ctor_kwargs
        self.requests: list[dict] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return iter([])


def test_zhipu_adapter_passes_explicit_timeout():
    # 不真启 SDK：手工接线 spy，只断言「超时值必须显式进入每一次请求」
    client = ZhipuChatClient.__new__(ZhipuChatClient)
    # 不真启 SDK：手工接线 spy
    client._apitimeout = 42.0
    spy = _SDKSpy(timeout=42.0)
    client._sdk = spy

    handle = client.stream(model="m", messages=[{"role": "user", "content": "hi"}])
    list(handle)  # 触发迭代
    assert spy.requests[0]["timeout"] == 42.0
    assert spy.requests[0]["stream"] is True

    client.complete(model="m", messages=[{"role": "user", "content": "hi"}])
    assert spy.requests[1]["timeout"] == 42.0
    assert spy.requests[1]["stream"] is False


def test_factory_unknown_provider_fails_fast(monkeypatch):
    from core import config

    monkeypatch.setattr(config, "LLM_PROVIDER", "antigravity-x")
    reset_chat_client()
    with pytest.raises(ValueError, match="未知 LLM_PROVIDER"):
        get_chat_client()
    reset_chat_client()


def test_factory_missing_key_degrades_to_none(monkeypatch):
    from core import config

    monkeypatch.setattr(config, "LLM_PROVIDER", "zhipu")
    monkeypatch.setattr(config, "API_KEY", "")
    reset_chat_client()
    assert get_chat_client() is None
    reset_chat_client()
