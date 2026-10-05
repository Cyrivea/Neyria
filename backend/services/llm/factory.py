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

_BUILDERS: dict[str, str] = {  # provider → 需要的密钥环境位（config 上的属性名）
    "zhipu": "API_KEY",
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
    key = getattr(config, _BUILDERS[provider], "")
    if not key:
        logger.warning("LLM 密钥缺失，聊天能力不可用（%s 未配置）", _BUILDERS[provider])
        _instance = None
    elif provider == "zhipu":
        from services.llm.zhipu import ZhipuChatClient

        _instance = ZhipuChatClient(key)
    _loaded = True
    return _instance


def reset_chat_client() -> None:
    """测试与热切换专用：清掉缓存的单例。"""
    global _instance, _loaded
    _instance = None
    _loaded = False
