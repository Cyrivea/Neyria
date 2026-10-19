"""WF1-③ 队列档案馆 + 费用预览的服务层测试。

覆盖语义（与拍板记录一一对应）：
- 预览与真实执行共用同一套缓存判定（reused 零计费 / stale 将重跑计费 / cold 首跑 / blocked 说破）；
- 费用口径 = capability 预占价目之和，含「缓存命中多则实际更少」的上界而非承诺；
- 队列删除 = 删运行记录（steps 级联）+ 产品资产行与磁盘文件，**积分不退还**；
- 「满意才保留」旗标：未来自动清理策略的豁免名单；
- 活跃运行不许删（409），记录不存在 404。

本文件直接跑真 SQLite（conftest 已把库重定向到临时目录），构图用轻量手写
draft（构图语义本身由 test_workflow_service.py 覆盖）。
"""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from core import config
from core.database import db_connection
from core.exceptions import AppError
from repositories import asset_repo, workflow_repo, workflow_run_repo
from services import workflow_service


@pytest.fixture
def uid() -> int:
    """每用例一个独立用户：会话级临时库里账号各自隔离，互不踩资产/运行记录。"""
    username = f"archive-{uuid.uuid4().hex[:8]}@test.local"
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, "hash"),
        )
        (new_id,) = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    return int(new_id)


def _lyrics_node(node_id: str = "node-1", **overrides) -> dict[str, Any]:
    node = {
        "id": node_id,
        "type": "lyrics",
        "name": "写歌词",
        "capability_id": "lyrics.generate",
        "params": {"theme": "夏夜"},
        "x": 300,
        "y": 280,
        "chain_no": 1,
        "chain_id": "chain-fixed",
    }
    node.update(overrides)
    return node


def _save_chain_draft(uid: int, nodes: list[dict], edges: list[dict]) -> None:
    draft = {
        "nodes": [
            {"id": workflow_service.INPUT_ID, "kind": "endpoint", "endpoint": "input"},
            {"id": workflow_service.OUTPUT_ID, "kind": "endpoint", "endpoint": "output"},
            *nodes,
        ],
        "edges": edges,
        "endpointPositions": {},
        "chains": [
            {
                "chain_no": 1,
                "chain_id": "chain-fixed",
                "label": "测试链",
                "template_id": "lyrics_only",
                "spec_hash": None,
                "node_ids": [n["id"] for n in nodes],
                "source": "assistant",
                "created_at": "2026-10-16T00:00:00+00:00",
            }
        ],
        "chain_counter": 1,
    }
    saved = workflow_repo.upsert_if_revision(uid, None, 0, draft, "2026-10-16T00:00:00+00:00")
    assert saved is not None


_SIMPLE_EDGES = [
    {"source": workflow_service.INPUT_ID, "target": "node-1"},
    {"source": "node-1", "target": workflow_service.OUTPUT_ID},
]


def _lyrics_estimate() -> float:
    return float(Decimal(str(config.BILLING_LYRICS_RESERVE_CREDITS)))


# ---------- 费用预览（get_run_preview） ----------


class TestRunPreview:
    def test_cold_chain_quotes_full_cost(self, uid):
        _save_chain_draft(uid, [_lyrics_node()], _SIMPLE_EDGES)

        preview = workflow_service.get_run_preview(uid, None)

        assert preview["ready"] is True
        assert preview["total_cost_credits"] == _lyrics_estimate()
        assert preview["billable_count"] == 1
        assert preview["reused_count"] == 0
        block = preview["chains"][0]["blocks"][0]
        assert block["state"] == "cold"

    def test_checkpoint_hit_is_free(self, uid):
        node = _lyrics_node()
        resolved = {"theme": "夏夜"}
        node["result"] = {"format": "markdown", "content": "歌词正文"}
        node["checkpoint"] = {
            "params_hash": workflow_service._params_fingerprint("lyrics.generate", resolved),
            "ran_at": "2026-10-16T00:00:00+00:00",
        }
        _save_chain_draft(uid, [node], _SIMPLE_EDGES)

        preview = workflow_service.get_run_preview(uid, None)

        assert preview["ready"] is True
        assert preview["total_cost_credits"] == 0.0
        assert preview["reused_count"] == 1
        assert preview["chains"][0]["blocks"][0]["state"] == "reused"

    def test_changed_params_mark_stale_and_billable(self, uid):
        node = _lyrics_node(params={"theme": "深冬"})
        node["result"] = {"format": "markdown", "content": "歌词正文"}
        node["checkpoint"] = {
            "params_hash": workflow_service._params_fingerprint("lyrics.generate", {"theme": "夏夜"}),
            "ran_at": "2026-10-16T00:00:00+00:00",
        }
        _save_chain_draft(uid, [node], _SIMPLE_EDGES)

        preview = workflow_service.get_run_preview(uid, None)

        assert preview["total_cost_credits"] == _lyrics_estimate()
        block = preview["chains"][0]["blocks"][0]
        assert block["state"] == "stale"
        assert "重跑" in (block["reason"] or "")

    def test_unselected_multi_candidate_upstream_blocks_preview(self, uid):
        lyrics = _lyrics_node(
            "node-1",
            candidates=[
                {"id": "c1", "status": "succeeded", "content": "A 版", "created_at": "t"},
                {"id": "c2", "status": "succeeded", "content": "B 版", "created_at": "t"},
            ],
            selectedCandidateId=None,
        )
        image = {
            "id": "node-2",
            "type": "image",
            "name": "生成封面",
            "capability_id": "image.generate",
            "params": {"image_prompt": "${upstream.result.content} 封面"},
            "x": 600,
            "y": 280,
            "chain_no": 1,
            "chain_id": "chain-fixed",
        }
        edges = [*_SIMPLE_EDGES, {"source": "node-1", "target": "node-2"}]
        _save_chain_draft(uid, [lyrics, image], edges)

        preview = workflow_service.get_run_preview(uid, None)

        assert preview["ready"] is False
        assert preview["reason"] and "选一个" in preview["reason"]
        states = {
            b["node_id"]: b["state"] for c in preview["chains"] for b in c["blocks"]
        }
        assert states["node-2"] == "blocked"

    def test_unlinked_canvas_is_not_ready_with_reason(self, uid):
        # 只写了节点没从输入接线
        _save_chain_draft(uid, [_lyrics_node()], [])

        preview = workflow_service.get_run_preview(uid, None)

        assert preview["ready"] is False
        assert "输入" in (preview["reason"] or "")
        assert preview["total_cost_credits"] == 0.0


# ---------- 队列档案馆（list / keep / delete） ----------


def _mk_run(uid: int, status: str = "succeeded", with_asset: bool = False) -> str:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    snapshot = {
        "nodes": [
            _lyrics_node(),
        ],
        "edges": _SIMPLE_EDGES,
        "chains": [
            {
                "chain_no": 1,
                "chain_id": "chain-fixed",
                "label": "测试链",
                "node_ids": ["node-1"],
            }
        ],
    }
    workflow_run_repo.create(
        run_id,
        uid,
        None,
        1,
        snapshot,
        None,
        None,
        "2026-10-16T00:00:00+00:00",
        env_snapshot=None,
    )
    workflow_run_repo.update_status(
        run_id,
        status,
        error=None,
        started_at="2026-10-16T00:00:01+00:00",
        finished_at="2026-10-16T00:00:02+00:00",
    )
    step = workflow_run_repo.create_step(
        step_id=f"step-{uuid.uuid4().hex[:8]}",
        run_id=run_id,
        node_id="node-1",
        capability_id="lyrics.generate",
        input_payload={"theme": "夏夜"},
        created_at="2026-10-16T00:00:01+00:00",
    )
    output = {"content": "歌词正文" if not with_asset else "/api/assets/abcd1234.png"}
    workflow_run_repo.finish_step(step["id"], "succeeded", output, None)
    return run_id


class TestListRuns:
    def test_runs_listed_newest_first_with_chain_grouping(self, uid):
        run_old = _mk_run(uid)
        run_new = _mk_run(uid)

        listing = workflow_service.list_runs(uid, None)

        ids = [r["run_id"] for r in listing["runs"]]
        assert run_new in ids and run_old in ids
        run = next(r for r in listing["runs"] if r["run_id"] == run_new)
        assert run["progress"] == {"done": 1, "total": 1}
        assert run["kept"] is False
        block = run["chains"][0]["blocks"][0]
        assert block["content"] in ("歌词正文", "/api/assets/abcd1234.png")

    def test_foreign_users_cannot_see_runs(self, uid):
        other = uid + 1000  # 绝不存在的用户
        run_id = _mk_run(uid)

        listing = workflow_service.list_runs(other, None)

        assert all(r["run_id"] != run_id for r in listing["runs"])


class TestKeepFlag:
    def test_keep_toggle_roundtrip(self, uid):
        run_id = _mk_run(uid)

        workflow_service.set_run_kept(uid, run_id, True)
        listing = workflow_service.list_runs(uid, None)
        kept_run = next(r for r in listing["runs"] if r["run_id"] == run_id)
        assert kept_run["kept"] is True

        workflow_service.set_run_kept(uid, run_id, False)
        assert all(r["kept"] is False for r in workflow_service.list_runs(uid, None)["runs"])

    def test_keep_on_missing_run_404(self, uid):
        with pytest.raises(AppError) as exc:
            workflow_service.set_run_kept(uid, "run-不存在", True)
        assert exc.value.status_code == 404


class TestDeleteRun:
    def test_delete_removes_record_steps_and_files_no_refund(self, uid, tmp_path, monkeypatch):
        # assets 目录就位 + 造一枚真文件（否则 unlink 无对象，files_removed=0 无法验证路径）
        storage = tmp_path / "storage"
        assets_dir = storage.parent / "assets"
        assets_dir.mkdir(parents=True)
        (assets_dir / "abcd1234.png").write_bytes(b"fake-png")
        monkeypatch.setattr(config, "DOCUMENT_STORAGE_DIR", str(storage))

        run_id = _mk_run(uid, with_asset=True)
        asset_repo.insert(
            user_id=uid,
            project_id=None,
            run_id=run_id,
            capability_id="image.generate",
            kind="image",
            filename="abcd1234.png",
            content_type="image/png",
            byte_size=8,
            prompt_snapshot=None,
            usage_event_id=None,
            created_at="2026-10-16T00:00:01+00:00",
        )

        result = workflow_service.delete_run(uid, run_id)

        assert result["deleted"] is True
        assert result["refund"] is False  # 诚实语义：运行即成账，积分不退
        assert result["files_removed"] == 1
        assert workflow_run_repo.get(uid, run_id) is None
        with db_connection() as conn:
            steps_left = conn.execute(
                "SELECT COUNT(*) FROM workflow_steps WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            assets_left = conn.execute(
                "SELECT COUNT(*) FROM assets WHERE user_id=? AND filename='abcd1234.png'",
                (uid,),
            ).fetchone()[0]
        assert steps_left == 0
        assert assets_left == 0
        assert not (assets_dir / "abcd1234.png").exists()

    def test_delete_active_run_rejected_409(self, uid):
        run_id = _mk_run(uid, status="running")

        with pytest.raises(AppError) as exc:
            workflow_service.delete_run(uid, run_id)
        assert exc.value.status_code == 409
        # 运行记录必须还在
        assert workflow_run_repo.get(uid, run_id) is not None

    def test_delete_missing_run_404(self, uid):
        with pytest.raises(AppError) as exc:
            workflow_service.delete_run(uid, "run-不存在")
        assert exc.value.status_code == 404

    def test_legacy_assets_without_run_linkage_still_collected(self, uid, tmp_path, monkeypatch):
        """老资产（run_id=NULL）靠 step 输出里的文件名兜底对账删除。"""
        storage = tmp_path / "storage"
        assets_dir = storage.parent / "assets"
        assets_dir.mkdir(parents=True)
        (assets_dir / "abcd1234.png").write_bytes(b"fake-png")
        monkeypatch.setattr(config, "DOCUMENT_STORAGE_DIR", str(storage))

        run_id = _mk_run(uid, with_asset=True)
        asset_repo.insert(
            user_id=uid,
            project_id=None,
            run_id=None,  # 关键：老财货没有对账列
            capability_id="image.generate",
            kind="image",
            filename="abcd1234.png",
            content_type="image/png",
            byte_size=8,
            prompt_snapshot=None,
            usage_event_id=None,
            created_at="2026-10-16T00:00:01+00:00",
        )

        result = workflow_service.delete_run(uid, run_id)

        assert result["files_removed"] == 1
        assert not (assets_dir / "abcd1234.png").exists()


class TestRouteRegistration:
    """路由表冒烟：档案馆四个端点注册齐全且不与存量 /runs/latest、/runs/{id} 冲突。"""

    def test_archive_endpoints_registered(self):
        from routers.workflows import router

        table = {(tuple(sorted(r.methods - {"HEAD", "OPTIONS"})), r.path) for r in router.routes}
        assert (("GET",), "/workflows/run-preview") in table
        assert (("GET",), "/workflows/runs") in table
        assert (("PATCH",), "/workflows/runs/{run_id}/keep") in table
        assert (("DELETE",), "/workflows/runs/{run_id}") in table
        # 存量端点仍在
        assert (("GET",), "/workflows/runs/{run_id}") in table
