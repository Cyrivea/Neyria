"""LLM client 工厂（C12）：按 LLM_PROVIDER 环境变量装配，进程级单例。

- 默认 zhipu；新增 Provider = 实现 ChatClient 协议 + 往 ``_BUILDERS`` 注册一行；
- 缺密钥不抛异常而是返回 None：调用方（编排器 503 / 分类器兜底）按原语义降级，
  与重构前行为保持一致；
- 未知 provider 属配置错误，立刻 raise 让启动即失败，不等第一次请求才炸。
"""

from __future__ import annotations

import logging

from core import config
from services.llm.types import ChatClient

logger = logging.getLogger("llm_factory")

# provider → (密钥属性名, 基础 URL 属性名)。zhipu 走 SDK（base 不适用）；
# openai = 任意 OpenAI 兼容网关（Kunbot、别家中转站均可复用，只换 env）。
_BUILDERS: dict[str, tuple[str, str | None]] = {
    "zhipu": ("API_KEY", None),
    "openai": ("KUNBOT_API_KEY", "KUNBOT_CHAT_BASE"),
}

_instance: ChatClient | None = None
_loaded = False


def get_chat_client() -> ChatClient | None:
    global _instance, _loaded
    if _loaded:
        return _instance
    provider = config.LLM_PROVIDER
    if provider not in _BUILDERS:
        raise ValueError(f"未知 LLM_PROVIDER={provider!r}，可选：{sorted(_BUILDERS)}")
    key_attr, base_attr = _BUILDERS[provider]
    key = getattr(config, key_attr, "")
    if not key:
        logger.warning("LLM 密钥缺失，聊天能力不可用（%s 未配置）", key_attr)
        _instance = None
    elif provider == "zhipu":
        from services.llm.zhipu import ZhipuChatClient

        _instance = ZhipuChatClient(key)
    elif provider == "openai":
        from services.llm.openai_compat import OpenAICompatChatClient

        _instance = OpenAICompatChatClient(key, getattr(config, base_attr or "", ""))
    else:  # pragma: no cover - 注册表外的 provider 在守卫处早就被拦了
        raise ValueError(f"未知 LLM_PROVIDER={provider!r}，可选：{sorted(_BUILDERS)}")
    _loaded = True
    return _instance


def reset_chat_client() -> None:
    """测试与热切换专用：清掉缓存的单例。"""
    global _instance, _loaded
    _instance = None
    _loaded = False
