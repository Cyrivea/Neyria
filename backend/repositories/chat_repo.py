import json

from core.database import db_connection


def append_message(
    user_id: int,
    role: str,
    content: str,
    project_id: int | None = None,
    attachments: list[dict] | None = None,
    citations: list[dict] | None = None,
    interrupted: bool = False,
) -> int:
    """落库并返回消息 id——前端需要 id 才能对指定消息评分/编辑重发（UI2）。"""
    with db_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO messages "
            "(user_id, project_id, role, content, attachments_json, citations_json, interrupted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                project_id,
                role,
                content,
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                json.dumps(citations, ensure_ascii=False) if citations else None,
                1 if interrupted else 0,
            ),
        )
        assert cursor.lastrowid is not None  # INSERT 刚成功必有 rowid
        return int(cursor.lastrowid)


def set_feedback(user_id: int, message_id: int, value: int | None) -> bool:
    """UI2-⑤：对指定账号自己的消息写/清评分。越权或消息不存在返回 False。"""
    with db_connection() as conn:
        cursor = conn.execute(
            "UPDATE messages SET feedback=? WHERE id=? AND user_id=?",
            (value, message_id, user_id),
        )
        return cursor.rowcount == 1


def truncate_from_message(user_id: int, project_id: int | None, message_id: int) -> bool:
    """UI2-②：编辑重发前置截断——从指定 user 消息起的所有后续消息全部删除。

    与 pop_last_turn 的差异：目标由调用方指定的消息 id 决定（不一定是尾轮），
    且后续的 user/system-notice 也一并删除——用户在前端确认框里显式同意了
    「此操作会删除该消息之后的 N 条对话」，这里只做归属与角色校验。
    返回是否实际截断（消息不存在 / 不属于该用户与工程 / 不是 user 消息 → False，调用方报 409）。
    """
    with db_connection() as conn:
        if project_id is None:
            target = conn.execute(
                "SELECT role FROM messages WHERE id=? AND user_id=? AND project_id IS NULL",
                (message_id, user_id),
            ).fetchone()
            scope_sql = "user_id=? AND project_id IS NULL AND id>=?"
            scope_params: tuple = (user_id, message_id)
        else:
            target = conn.execute(
                "SELECT role FROM messages WHERE id=? AND user_id=? AND project_id=?",
                (message_id, user_id, project_id),
            ).fetchone()
            scope_sql = "user_id=? AND project_id=? AND id>=?"
            scope_params = (user_id, project_id, message_id)
        if target is None or target[0] != "user":
            return False
        conn.execute(f"DELETE FROM messages WHERE {scope_sql}", scope_params)
        return True


def list_history(user_id: int, project_id: int | None = None) -> list[dict]:
    with db_connection() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT id, role, content, attachments_json, citations_json, interrupted, feedback"
                " FROM messages WHERE user_id=? AND project_id IS NULL ORDER BY id",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, role, content, attachments_json, citations_json, interrupted, feedback"
                " FROM messages WHERE user_id=? AND project_id=? ORDER BY id",
                (user_id, project_id),
            ).fetchall()
    history: list[dict] = []
    for msg_id, role, content, attachments_json, citations_json, interrupted, feedback in rows:
        item = {"id": msg_id, "role": role, "content": content}
        if interrupted:
            item["interrupted"] = True
        if feedback is not None:
            item["feedback"] = feedback
        if attachments_json:
            try:
                item["attachments"] = json.loads(attachments_json)
            except json.JSONDecodeError:
                item["attachments"] = []
        if citations_json:
            try:
                item["citations"] = json.loads(citations_json)
            except json.JSONDecodeError:
                item["citations"] = []
        history.append(item)
    return history


def pop_last_turn(user_id: int, project_id: int | None = None) -> bool:
    """重生成前置截断（UI1）：删除『最后一条 user 消息 + 其后的所有 assistant 消息』。

    返回是否实际截断。拒绝条件（返回 False，调用方应报 409）：
    - 根本没有 user 消息；
    - 最后一条 user 之后没有 assistant 尾巴；
    - 尾巴里混入 system-notice 等其他角色（说明最近一轮之后又发生了新事件，
      此时重生成会让上下文漂移，必须由用户显式重新提问）。
    """
    with db_connection() as conn:
        if project_id is None:
            scope_sql = "SELECT id, role FROM messages WHERE user_id=? AND project_id IS NULL ORDER BY id"
            rows = conn.execute(scope_sql, (user_id,)).fetchall()
        else:
            scope_sql = "SELECT id, role FROM messages WHERE user_id=? AND project_id=? ORDER BY id"
            rows = conn.execute(scope_sql, (user_id, project_id)).fetchall()

        last_user_pos: int | None = None
        for pos in range(len(rows) - 1, -1, -1):
            if rows[pos][1] == "user":
                last_user_pos = pos
                break
        if last_user_pos is None:
            return False

        tail = rows[last_user_pos + 1 :]
        if not tail or any(role != "assistant" for _, role in tail):
            return False

        doomed_ids = [row_id for row_id, _ in tail]
        doomed_ids.append(rows[last_user_pos][0])
        conn.execute(
            f"DELETE FROM messages WHERE id IN ({','.join('?' * len(doomed_ids))})",
            doomed_ids,
        )
        return True


def clear_history(user_id: int, project_id: int | None = None) -> None:
    with db_connection() as conn:
        if project_id is None:
            conn.execute("DELETE FROM messages WHERE user_id=? AND project_id IS NULL", (user_id,))
        else:
            conn.execute("DELETE FROM messages WHERE user_id=? AND project_id=?", (user_id, project_id))
