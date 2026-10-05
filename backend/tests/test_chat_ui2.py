"""UI2 后端测试：消息评分（⑤）、编辑重发截断（②）、追问建议事件（③）。

仓库层进独立临时库验证语义（不碰生产库，与 C22 同向）；编排层只验证
调用时机、互斥 400、拒绝路径 409 与 suggestions 事件的降级行为。
"""

import asyncio
import json
import sqlite3

import pytest
from fastapi import HTTPException

import core.database
import services.chat.chat_orchestrator as chat_orchestrator
from repositories.chat_repo import (
    append_message,
    list_history,
    set_feedback,
    truncate_from_message,
)
from services.llm import ChatReply, StreamDelta

MESSAGES_DDL = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    attachments_json TEXT,
    citations_json TEXT,
    interrupted INTEGER DEFAULT 0,
    feedback INTEGER
)
"""


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """独立临时库：绝不碰生产 conversations.db（C22 样板）。"""
    db_file = tmp_path / "ui2_test.db"
    monkeypatch.setattr(core.database, "DB_FILE", db_file)
    conn = sqlite3.connect(db_file)
    conn.execute(MESSAGES_DDL)
    conn.execute(
        "INSERT INTO messages (user_id, project_id, role, content) VALUES "
        "(1, 42, 'user', '第一问'), (1, 42, 'assistant', '第一答'),"
        "(1, 42, 'user', '第二问'), (1, 42, 'assistant', '第二答'),"
        "(1, 42, 'user', '第三问'), (1, 42, 'assistant', '第三答')"
    )
    conn.commit()
    conn.close()
    return db_file


def _rows(user_id=1, project_id=42):
    return list_history(user_id, project_id)


# ---------- 仓库层：set_feedback（UI2-⑤） ----------


def test_feedback_roundtrip_and_clear(fresh_db):
    target = _rows()[1]  # 第一答
    assert "id" in target and "feedback" not in target  # 未评时不出键

    assert set_feedback(1, target["id"], 1) is True
    assert _rows()[1]["feedback"] == 1

    assert set_feedback(1, target["id"], -1) is True  # 切换
    assert _rows()[1]["feedback"] == -1

    assert set_feedback(1, target["id"], None) is True  # 取消
    assert "feedback" not in _rows()[1]


def test_feedback_rejects_foreign_or_missing_message(fresh_db):
    mine = _rows()[1]["id"]
    assert set_feedback(999, mine, 1) is False  # 越权
    assert set_feedback(1, 424242, 1) is False  # 不存在


# ---------- 仓库层：truncate_from_message（UI2-②） ----------


def test_truncate_from_middle_user_message(fresh_db):
    second_user = _rows()[2]  # 第二问
    assert truncate_from_message(1, 42, second_user["id"]) is True
    assert [r["role"] for r in _rows()] == ["user", "assistant"]  # 第二问起全删


def test_truncate_from_deletes_system_notice_too(fresh_db):
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO messages (user_id, project_id, role, content) "
        "VALUES (1, 42, 'system-notice', '工作流已完成')"
    )
    conn.commit()
    conn.close()
    third_user = _rows()[4]
    assert truncate_from_message(1, 42, third_user["id"]) is True
    assert len(_rows()) == 4  # notice 跟随尾巴一起消失


def test_truncate_from_rejects_invalid_targets(fresh_db):
    rows = _rows()
    assistant_row = rows[1]
    assert truncate_from_message(1, 42, assistant_row["id"]) is False  # 只能锚定 user 消息
    assert truncate_from_message(999, 42, rows[0]["id"]) is False  # 越权
    assert truncate_from_message(1, 43, rows[0]["id"]) is False  # 其他工程的消息
    assert truncate_from_message(1, 42, 424242) is False  # 不存在
    assert len(_rows()) == 6  # 全部拒绝路径一丝未动


def test_append_message_returns_row_id(fresh_db):
    new_id = append_message(1, "user", "第四问", 42)
    assert isinstance(new_id, int)
    assert _rows()[-1]["id"] == new_id


# ---------- 编排层：edit_from_id / suggestions ----------


def content_stream(*texts):
    """C12 后的假流：直接产出抽象层中立类型 StreamDelta。"""
    return [StreamDelta(content=t) for t in texts]


class FakeStream:
    def __init__(self, deltas):
        self._deltas = deltas

    def __iter__(self):
        return iter(self._deltas)

    def close(self):
        pass


class FakeClient:
    """C12 后的测试双。script 项为 str=流式回复；Exception 实例=非流式追问时抛错。"""

    def __init__(self, script):
        self.calls = []
        self._script = list(script)

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(content_stream(self._script.pop(0)))

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):  # 追问建议返回体：{"content": "[...]"}
            return ChatReply(content=item["content"])
        return ChatReply(content=str(item))


def _run_collect(user_message, **kwargs):
    events = []

    async def collect():
        async for raw in chat_orchestrator.stream_chat({"id": 1}, user_message, 42, **kwargs):
            events.append(json.loads(raw.removeprefix("data: ").strip()))

    asyncio.run(collect())
    return events


def _patch_common(monkeypatch, order, fake_client, truncate_ret=True):
    # conftest 默认关掉了追问建议（防打破存量用例的调用次数断言），明测它再打开
    monkeypatch.setattr("core.config.FOLLOWUP_SUGGESTIONS_ENABLED", True)
    monkeypatch.setattr(
        chat_orchestrator,
        "truncate_from_message",
        lambda uid, pid, mid: (order.append(("truncate", mid)) or True) and truncate_ret,
    )

    async def fake_context(user_id, message, project_id=None, attachments=None):
        order.append("ctx")
        from services.chat.context_builder import ChatContext

        return ChatContext(
            messages=[{"role": "system", "content": "SYS"}, {"role": "user", "content": message}],
            clean_history=[{"role": "user", "content": message}],
            persist_text=message,
            attachments=[],
        )

    monkeypatch.setattr(chat_orchestrator, "build_chat_context", fake_context)
    monkeypatch.setattr(chat_orchestrator, "append_message", lambda *a, **kw: 777)
    monkeypatch.setattr(chat_orchestrator, "client", fake_client)


def test_orchestrator_edit_truncates_before_context(monkeypatch):
    order: list = []
    fake = FakeClient(["改写后的答案", {"content": '["追问一","追问二"]'}])
    _patch_common(monkeypatch, order, fake)

    done = [e for e in _run_collect("改写后的第二问", edit_from_id=123) if e["type"] == "done"]
    assert order == [("truncate", 123), "ctx"]  # 先截库再建上下文（否则模型还看得见旧轮）
    assert done  # 正常走完

    calls = [e for e in order]
    assert calls[0][0] == "truncate"


def test_orchestrator_edit_rejects_bad_target(monkeypatch):
    order: list = []
    fake = FakeClient([])
    _patch_common(monkeypatch, order, fake, truncate_ret=False)

    with pytest.raises(HTTPException) as exc:
        _run_collect("改写", edit_from_id=999)
    assert exc.value.status_code == 409
    assert order == [("truncate", 999)]  # 截断失败即止步，不建上下文不烧模型


def test_orchestrator_regenerate_and_edit_are_mutually_exclusive():
    with pytest.raises(HTTPException) as exc:
        _run_collect("冲突", regenerate=True, edit_from_id=1)
    assert exc.value.status_code == 400


def test_suggestions_event_arrives_after_done(monkeypatch):
    order: list = []
    fake = FakeClient(["完整答案", {"content": '["下一步要不要配个封面图？","追问二", "超长' + "字" * 60 + '"]'}])
    _patch_common(monkeypatch, order, fake)

    events = _run_collect("展示一个快排代码示例", edit_from_id=5)
    done_idx = next(i for i, e in enumerate(events) if e["type"] == "done")
    sug = [e for e in events if e["type"] == "suggestions"]
    assert sug, "正常路径应补发 suggestions 事件"
    # suggestions 必须在 done 之后到达（不阻塞正文上屏）
    assert events.index(sug[0]) > done_idx
    assert sug[0]["items"][0] == "下一步要不要配个封面图？"
    assert len(sug[0]["items"][2]) == 40  # 超长截断


def test_suggestions_silently_degrade_on_model_error(monkeypatch):
    order: list = []
    fake = FakeClient(["答案", RuntimeError("上游挂了")])
    _patch_common(monkeypatch, order, fake)

    events = _run_collect("展示一个快排代码示例", edit_from_id=5)
    assert any(e["type"] == "done" for e in events)  # 主路径不受影响
    assert not any(e["type"] == "suggestions" for e in events)  # 失败=静默没有该事件
