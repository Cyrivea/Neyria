"""usage_events 精确用量账本的持久化写入。"""

from __future__ import annotations

import sqlite3
from typing import Any

from core.database import db_connection

_COLUMNS = (
    "id, user_id, project_id, capability_id, source, model, prompt_tokens, "
    "completion_tokens, total_tokens, status, error, duration_ms, created_at"
)


def insert(
    user_id: int,
    project_id: int | None,
    capability_id: str,
    source: str,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    status: str,
    error: str | None,
    duration_ms: float | None,
    created_at: str,
) -> dict[str, Any]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        # 占位符数量必须由列清单推导——手写常数在列变动后会静默错位（实测
        # 生产上 '13 values for 12 columns'，账本一行都没写进去）。
        insert_columns = _COLUMNS.removeprefix("id, ")
        placeholders = ", ".join("?" for _ in insert_columns.split(","))
        cursor = conn.execute(
            f"INSERT INTO usage_events ({insert_columns}) VALUES ({placeholders})",
            (
                user_id,
                project_id,
                capability_id,
                source,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                status,
                error,
                duration_ms,
                created_at,
            ),
        )
        event_id = cursor.lastrowid
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM usage_events WHERE id=?", (event_id,)
        ).fetchone()
    assert row is not None
    return dict(row)


def list_for_user(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM usage_events WHERE user_id=? "
            "ORDER BY datetime(created_at) DESC LIMIT ?",
            (user_id, max(1, min(limit, 500))),
        ).fetchall()
    return [dict(row) for row in rows]


def summarize_for_user_since(user_id: int, since_iso: str) -> list[dict[str, Any]]:
    """按能力聚合某时刻起的用量（计费页「近 N 天用量」数据源）。

    billed_credits_micro 只在计费结算挂过号的行上有值（BILLING_MODE=off 时为 NULL），
    聚合时对 NULL 记 0——调用次数永远真实，金额随计费开关逐渐丰满。
    """
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT capability_id,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN status='succeeded' THEN 1 ELSE 0 END) AS succeeded,
                   COALESCE(SUM(billed_credits_micro), 0) AS billed_micro
            FROM usage_events
            WHERE user_id=? AND datetime(created_at) >= datetime(?)
            GROUP BY capability_id
            ORDER BY billed_micro DESC
            """,
            (user_id, since_iso),
        ).fetchall()
    return [dict(row) for row in rows]


def count_for_user(user_id: int) -> int:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE user_id=?", (user_id,)
        ).fetchone()
    return int(row[0])
