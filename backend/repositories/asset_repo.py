"""资产表 repo：每次 capability 产出（/api/assets/...）都对应账本一行。


资产文件命名本身是最外层的不可信任标识（uuid hex），库里这一行才是
{谁, 哪个项目, 哪次运行， 调用哪个能力， 花多少钱} 的可查询事实。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from core.database import db_connection

_COLUMNS = (
    "id, user_id, project_id, run_id, capability_id, kind, filename, content_type, "
    "byte_size, prompt_snapshot_json, usage_event_id, created_at"
)


def _row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    if item.get("prompt_snapshot_json"):
        import json

        item["prompt_snapshot"] = json.loads(item.pop("prompt_snapshot_json"))
    else:
        item["prompt_snapshot"] = None
    return item


def insert(
    *,
    user_id: int,
    project_id: int | None,
    run_id: str | None,
    capability_id: str,
    kind: str,
    filename: str,
    content_type: str,
    byte_size: int,
    prompt_snapshot: dict[str, Any] | None,
    usage_event_id: int | None,
    created_at: str,
) -> dict[str, Any]:
    asset_id = f"asset-{uuid.uuid4().hex[:12]}"
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            f"INSERT INTO assets ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                asset_id,
                user_id,
                project_id,
                run_id,
                capability_id,
                kind,
                filename,
                content_type,
                byte_size,
                json.dumps(prompt_snapshot, ensure_ascii=False) if prompt_snapshot else None,
                usage_event_id,
                created_at,
            ),
        )
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM assets WHERE id=?", (asset_id,)
        ).fetchone()
    assert row is not None
    return _row(row)


def get_owned(asset_id: str, user_id: int) -> dict[str, Any] | None:
    """属主安全的资产查询：越权访问一律返回 None（与“不存在”同口径，不泄露他人资产存在性）。"""
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM assets WHERE id=? AND user_id=?", (asset_id, user_id)
        ).fetchone()
    return _row(row) if row else None


def get_owned_by_filename(filename: str, user_id: int) -> dict[str, Any] | None:
    """按 /api/assets/<filename> 路径回收资产——助手在对话历史里只看到这种形态。"""
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM assets WHERE filename=? AND user_id=?",
            (filename, user_id),
        ).fetchone()
    return _row(row) if row else None


def list_for_user(
    user_id: int, project_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        if project_id is None:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM assets WHERE user_id=? AND project_id IS NULL "
                f"ORDER BY created_at DESC LIMIT ?",
                (user_id, min(limit, 500)),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM assets WHERE user_id=? AND project_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, project_id, min(limit, 500)),
            ).fetchall()
    return [_row(r) for r in rows]


def list_for_run(user_id: int, run_id: str) -> list[dict[str, Any]]:
    """WF1-③ 档案馆删除路径：按 run_id 对账列捞某次运行产出的全部资产行。"""
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM assets WHERE user_id=? AND run_id=?",
            (user_id, run_id),
        ).fetchall()
    return [_row(r) for r in rows]


def delete_by_filename(user_id: int, filename: str) -> list[dict[str, Any]]:
    """删除某用户名下指定文件名的资产行，返回被删的行（供调用方连磁盘文件一起清）。

    只按 user 隔离删除（不带 project 条件）：同一文件在两库行里共存时一并清账，
    磁盘文件随后只 unlink 一次。
    """
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM assets WHERE user_id=? AND filename=?",
            (user_id, filename),
        ).fetchall()
        conn.execute(
            "DELETE FROM assets WHERE user_id=? AND filename=?", (user_id, filename)
        )
    return [_row(r) for r in rows]


def delete_by_run(user_id: int, run_id: str) -> list[dict[str, Any]]:
    """删除对账到某次运行的全部资产行，返回被删的行。"""
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM assets WHERE user_id=? AND run_id=?",
            (user_id, run_id),
        ).fetchall()
        conn.execute("DELETE FROM assets WHERE user_id=? AND run_id=?", (user_id, run_id))
    return [_row(r) for r in rows]
