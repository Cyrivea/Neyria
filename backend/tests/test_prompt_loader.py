"""prompt 文件化装配（C17）：目录为唯一事实源，占位符只注入 profile。"""

from agents import neyria
from agents.prompt_loader import reload_cache


def test_sections_assembled_in_filename_order():
    prompt = neyria.build_system_prompt("", "")
    # 四个段落的标志性开头必须按序出现
    idx_id = prompt.index("你是Neyria")
    idx_kb = prompt.index("你有两类信息来源")
    idx_tools = prompt.index("工具使用规则：")
    assert idx_id < idx_kb < idx_tools


def test_profile_placeholder_only_variable():
    prompt = neyria.build_system_prompt("喜欢国风和 lo-fi", "")
    assert "喜欢国风和 lo-fi" in prompt
    # 花括号模板语法绝不能残留在发往模型的文本里
    assert "{profile_section}" not in prompt
    # 规则里的花括号字面量（旧式文本工具调用举例）必须原样保留，不被当模板变量炸掉
    assert 'search_web {"query"' in prompt


def test_unknown_placeholders_passthrough():
    # 未来的增量规则文件里再有别的 {...} 不能炸装配
    prompt = neyria.build_system_prompt("", "")
    assert prompt.endswith("\n")

def test_reload_cache_refreshes():
    reload_cache()
    prompt = neyria.build_system_prompt("", "")
    assert "Neyria" in prompt
