"""意图分类器（C16）：规则秒判 + LLM 保底 + 缓存行为。"""



from services.chat.intent_classifier import (
    CONFIDENCE_GATE,
    _rule_classify,
    reset_cache,
)


def setup_function():
    reset_cache()


def test_rule_calc_intent():
    intent, conf = _rule_classify("12*34 等于多少")
    assert intent == "calc"
    assert conf >= CONFIDENCE_GATE


def test_rule_status_intent():
    intent, _ = _rule_classify("跑得怎么样了")
    assert intent == "status"


def test_rule_kb_intent():
    intent, _ = _rule_classify("我的数据在哪")
    assert intent == "kb"


def test_rule_modify_intent():
    intent, _ = _rule_classify("清空画布重做")
    assert intent == "modify"


def test_no_rule_matches_blank():
    intent, conf = _rule_classify("你好呀")
    assert intent is None
    assert conf == 0.0


def test_tools_for_calc():
    from services.chat.intent_classifier import tools_for_intent

    assert "calculate" in tools_for_intent("calc")
    assert "search_web" not in tools_for_intent("calc")
    assert "add_workflow_chains" not in tools_for_intent("calc")


def test_tools_for_chat():
    from services.chat.intent_classifier import tools_for_intent

    allowed = tools_for_intent("chat", include_all=False)
    assert allowed == set()  # 聊天场景不挂副作用


def test_tools_create_keeps_clear_draft():
    from services.chat.intent_classifier import tools_for_intent

    assert "clear_workflow_draft" in tools_for_intent("create")
