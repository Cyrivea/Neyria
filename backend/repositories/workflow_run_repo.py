"""Workflow run / step 账本的持久化访问层。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from core.database import db_connection

_RUN_COLUMNS = (
    "id, user_id, project_id, workflow_id, draft_revision, draft_snapshot_json, "
    "status, error, job_id, created_at, started_at, finished_at, env_snapshot_json, kept"
)
_STEP_COLUMNS = (
    "id, run_id, node_id, capability_id, status, input_json, output_json, error, created_at"
)


def _run(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["draft_snapshot"] = json.loads(item.pop("draft_snapshot_json"))
    env_raw = item.pop("env_snapshot_json", None)
    item["env_snapshot"] = json.loads(env_raw) if env_raw else None
    return item


def _step(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    # 注意：必须先 pop 存值再 loads。写成 `json.loads(item["input_json"]) if item.pop("input_json")`
    # 会在三元条件里先把 key pop 掉，然后真值分支再访问同 key → KeyError('input_json')。
    # 生产事故实证：workflow_run 任务全部挂在 create_step 返回处，重试 3 次耗尽。
    input_raw = item.pop("input_json")
    output_raw = item.pop("output_json")
    item["input"] = json.loads(input_raw) if input_raw else None
    item["output"] = json.loads(output_raw) if output_raw else None
    return item


def create(
    run_id: str,
    user_id: int,
    project_id: int | None,
    draft_revision: int,
    draft_snapshot: dict[str, Any],
    workflow_id: int | None,
    job_id: str | None,
    created_at: str,
    env_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            f"INSERT INTO workflow_runs ({_RUN_COLUMNS}) "
            "VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, ?, ?, NULL, NULL, ?, 0)",
            (
                run_id,
                user_id,
                project_id,
                workflow_id,
                draft_revision,
                json.dumps(draft_snapshot, ensure_ascii=False),
                job_id,
                created_at,
                json.dumps(env_snapshot, ensure_ascii=False) if env_snapshot else None,
            ),
        )
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM workflow_runs WHERE id=?", (run_id,)
        ).fetchone()
    assert row is not None
    return _run(row)


def get(user_id: int, run_id: str) -> dict[str, Any] | None:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM workflow_runs WHERE id=? AND user_id=?",
            (run_id, user_id),
        ).fetchone()
    return _run(row) if row else None


def find_active_for_workflow(user_id: int, workflow_id: int | None) -> dict[str, Any] | None:
    """检查某一份 Draft 当前是否有 queued/running 的运行；编辑锁用它拒绝写入。"""
    if workflow_id is None:
        return None
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM workflow_runs "
            "WHERE workflow_id=? AND user_id=? AND status IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT 1",
            (workflow_id, user_id),
        ).fetchone()
    return _run(row) if row else None


def list_recent(user_id: int, project_id: int | None, limit: int = 5) -> list[dict[str, Any]]:
    """某项目最近的运行记录（新→旧）；默认工作区 project_id 用 COALESCE 匹配。"""
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT {_RUN_COLUMNS} FROM workflow_runs "
            "WHERE user_id=? AND COALESCE(project_id, -1)=COALESCE(?, -1) "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, project_id, max(1, min(limit, 20))),
        ).fetchall()
    return [_run(row) for row in rows]


def set_kept(user_id: int, run_id: str, kept: bool) -> bool:
    """WF1-③「满意才保留」旗标。返回是否有行被改动（供路由判 404）。"""
    with db_connection() as conn:
        cursor = conn.execute(
            "UPDATE workflow_runs SET kept=? WHERE id=? AND user_id=?",
            (1 if kept else 0, run_id, user_id),
        )
    return cursor.rowcount > 0


def delete_run(user_id: int, run_id: str) -> str | None:
    """删除一条运行记录；workflow_steps 靠外键 ON DELETE CASCADE 级联清掉。

    活跃运行（queued/running）拒绝删除，返回其状态；未能删除/不存在返回 None 的语义
    交由服务层决定（不存在→404、进行中→409）。返回终态字符串表示删成了。
    """
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM workflow_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ).fetchone()
        if row is None:
            return None
        status = str(row["status"])
        if status in ("queued", "running"):
            return f"active:{status}"
        conn.execute(
            "DELETE FROM workflow_runs WHERE id=? AND user_id=?", (run_id, user_id)
        )
    return status


def update_status(
    run_id: str,
    status: str,
    *,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> bool:
    with db_connection() as conn:
        return (
            conn.execute(
                "UPDATE workflow_runs SET status=?, error=COALESCE(?, error), "
                "started_at=COALESCE(?, started_at), finished_at=COALESCE(?, finished_at) "
                "WHERE id=?",
                (status, error, started_at, finished_at, run_id),
            ).rowcount
            == 1
        )


def create_step(
    step_id: str,
    run_id: str,
    node_id: str,
    capability_id: str,
    input_payload: dict[str, Any] | None,
    created_at: str,
) -> dict[str, Any]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            f"INSERT INTO workflow_steps ({_STEP_COLUMNS}) VALUES (?, ?, ?, ?, 'queued', ?, NULL, NULL, ?)",
            (
                step_id,
                run_id,
                node_id,
                capability_id,
                json.dumps(input_payload, ensure_ascii=False) if input_payload else None,
                created_at,
            ),
        )
        row = conn.execute(
            f"SELECT {_STEP_COLUMNS} FROM workflow_steps WHERE id=?", (step_id,)
        ).fetchone()
    assert row is not None
    return _step(row)


def finish_step(
    step_id: str,
    status: str,
    output_payload: dict[str, Any] | None,
    error: str | None,
) -> bool:
    with db_connection() as conn:
        return (
            conn.execute(
                "UPDATE workflow_steps SET status=?, output_json=?, error=? WHERE id=?",
                (
                    status,
                    json.dumps(output_payload, ensure_ascii=False) if output_payload else None,
                    error,
                    step_id,
                ),
            ).rowcount
            == 1
        )


def list_steps(user_id: int, run_id: str) -> list[dict[str, Any]]:
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        # 注意是 ", ".join：直接 "s.".join 会拼出 "s.ids.run_id" 这类坏 SQL（实测 500）
        step_columns = ", ".join(f"s.{col.strip()}" for col in _STEP_COLUMNS.split(","))
        rows = conn.execute(
            f"SELECT {step_columns} FROM workflow_steps s "
            "JOIN workflow_runs r ON r.id = s.run_id "
            "WHERE s.run_id=? AND r.user_id=? ORDER BY s.created_at ASC",
            (run_id, user_id),
        ).fetchall()
    return [_step(row) for row in rows]
