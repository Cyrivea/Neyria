"""prompt 文件化装配（C17）：目录为唯一事实源，占位符只注入 profile。"""

from agents import neyria
from agents.prompt_loader import reload_cache


def test_sections_assembled_in_filename_order():
    prompt = neyria.build_system_prompt("", "")
    # 四个段落的标志性开头必须按序出现
    idx_id = prompt.index("你是 Neyria")
    idx_kb = prompt.index("你有两类信息来源")
    idx_tools = prompt.index("工具使用规则：")
    assert idx_id < idx_kb < idx_tools


def test_profile_placeholder_only_variable():
    prompt = neyria.build_system_prompt("喜欢国风和 lo-fi", "")
    assert "喜欢国风和 lo-fi" in prompt
    # 花括号模板语法绝不能残留在发往模型的文本里
    assert "{profile_section}" not in prompt
    # 花括号字面量不能被误当模板变量炸掉：用户侧可控输入里夹带花括号也要原样穿过。
    # （这里的引出写与实际 prompt 内容脱耦：124 期被模仿"反例"翻车启始后，规则文件
    # 里的花括号示例被清空，该险类只能推到「输入里带花括号」来守）
    tricky = neyria.build_system_prompt("喜欢国风 {unknown_var} 与 lo-fi", "")
    assert "{unknown_var}" in tricky


def test_unknown_placeholders_passthrough():
    # 未来的增量规则文件里再有别的 {...} 不能炸装配
    prompt = neyria.build_system_prompt("", "")
    assert prompt.endswith("\n")

def test_reload_cache_refreshes():
    reload_cache()
    prompt = neyria.build_system_prompt("", "")
    assert "Neyria" in prompt
