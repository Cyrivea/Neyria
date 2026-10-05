"""历史兼容模块：system prompt 装配。

LLM client 已于 2026-10-03 迁移到 ``services/llm``（C12 抽象层）：
这里不再直接构造任何 Provider SDK。要拿 client 请走
``services.llm.get_chat_client()``。
"""

from agents.prompt_loader import build_base_prompt


def build_system_prompt(profile: str, context: str) -> str:
    """主体规则全部来自 prompts/*.md 文件装配（C17），这里只负责运行时变量注入。"""
    profile_section = f"\n用户信息：\n{profile}" if profile else ""
    prompt = build_base_prompt(profile_section)
    if context:
        prompt += (
            "\n\n<knowledge_base_context>\n"
            "以下内容是后端已经从用户当前项目的私有 RAG 知识库检索出的结果。"
            "请直接把它当作可用资料回答用户问题：\n"
            f"{context}\n"
            "</knowledge_base_context>\n"
            "- 格式要求：请务必使用 Markdown 格式输出。"
        )
    return prompt
