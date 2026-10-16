"""真实 SQLite 集成测试（B23~B25 事故的直接防回归）。

与 test_workflow_service.py 的全 stub 单测互补：本文件把所有数据库操作
打在一份临时 SQLite 上跑完整生命周期——提交运行 → Worker 执行 →
步骤账本落库 → 状态查询 → 崩溃收尸。repositories 层不允许被 stub，
这正是生产事故('_step' KeyError、list_steps SQL 拼写错)钻过的空子。
"""



import asyncio
import copy

import pytest

import core.database as database
from core.migrations import run_migrations
from repositories import workflow_run_repo
from services import workflow_service
from services.workflow_service import submit_workflow_run


@pytest.fixture(autouse=True)
def _migrated_db(real_db):
    """自动跑迁移到 latest；这个测试文件不走真实库会什么都通不了。"""
    run_migrations()


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setattr(database, "DB_FILE", db)
    run_migrations()
    return db


def _create_user(user_id: int) -> None:
    """workflow_drafts 有指向 users 的外键，真库测试必须先把用户建出来。"""
    with database.db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, profile, created_at) "
            "VALUES (?, ?, 'x', '', '2026-01-01')",
            (user_id, f"inttest-{user_id}@local"),
        )


def _ensure_user(user_id: int) -> None:
    with database.db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, profile, created_at) "
            "VALUES (?, ?, 'x', '', '2026-01-01')",
            (user_id, f"itest-{user_id}@b60x.test"),
        )


def _add_lyrics_chain(user_id: int):
    _ensure_user(user_id)
    workflow_service.mark_canvas_read(user_id, None)
    return workflow_service.add_workflow_chains(
        user_id,
        None,
        [{"template_id": "lyrics_only", "slots": {"theme": "失恋", "style": "流行抒情"}}],
    )


def test_full_run_lifecycle_with_real_repos(real_db, monkeypatch):
    """提交 → 执行 → 步骤可见 → Draft 终态，全流程真库。"""
    from schemas.capability import CapabilityResult

    user = 900
    _create_user(user)
    _add_lyrics_chain(user)

    async def fake_capability(capability_id, inputs, *, context):
        return CapabilityResult(
            capability_id=capability_id,
            status="succeeded",
            result={"format": "markdown", "content": "测试歌词正文"},
        )

    monkeypatch.setattr(workflow_service, "run_capability", fake_capability)

    submission = submit_workflow_run(user, None)
    assert submission["status"] == "queued"

    # 驱动 Worker  handler 同款入口（asyncio.run 装进同步函数）
    result = workflow_service.execute_workflow_run_job(
        job_id="job-test",
        run_id=submission["run_id"],
        draft_snapshot=submission["draft"],
        user_id=user,
        project_id=None,
    )
    assert result["status"] == "succeeded"

    # 运行步骤账本落库真实可查（此前 list_steps 拼坏 SQL 的地方）
    detail = workflow_service.get_run_status_detail(user, None, submission["run_id"])
    assert detail["status"] == "succeeded"
    assert detail["progress"] == {"done": 1, "total": 1}
    assert detail["steps"][0]["status"] == "succeeded"
    assert detail["steps"][0]["node_name"] == "歌词生成"

    # Draft 画布节点终态 = succeeded
    final = workflow_service.get_draft(user, None)
    node = next(n for n in final["draft"]["nodes"] if n.get("capability_id"))
    assert node["runStatus"] == "succeeded"
    assert node["result"]["content"] == "测试歌词正文"


def test_crash_finalization_releases_lock(real_db):
    """收尸：运行中断后 running 变 failed，编辑锁据 active=run 判定解除。"""
    user = 901
    _create_user(user)
    _add_lyrics_chain(user)
    submission = submit_workflow_run(user, None)

    # 人为卡住：把 running_draft 落到库，再崩溃
    workflow_service.finalize_failed_run(user, None, submission["run_id"], "RuntimeError: boom")

    detail = workflow_service.get_run_status_detail(user, None, submission["run_id"])
    assert detail["status"] == "failed"
    draft = workflow_service.get_draft(user, None)["draft"]
    stuck = [n for n in draft["nodes"] if n.get("runStatus") == "running"]
    assert not stuck

    # 收尸后可以重新保存编辑（不再被 WORKFLOW_BUSY 顶住）
    current = workflow_service.get_draft(user, None)
    saved = workflow_service.save_draft(user, None, current["draft"], current["revision"])
    assert saved["revision"] == current["revision"] + 1


def test_unwired_upstream_ref_fails_node_honestly(real_db):
    """手画节点 prompt 里夹带上游引用、却只有 Input 供词：运行期必须如实失败，
    不能让引用记号原样烧给上游 API，更不能盲跑。"""
    user = 902
    _create_user(user)
    workflow_service.mark_canvas_read(user, None)
    current = workflow_service.get_draft(user, None)
    draft = copy.deepcopy(current["draft"])
    draft["nodes"].append(
        {
            "id": "hand-img",
            "kind": "tool",
            "capability_id": "image.generate",
            "name": "图像生成",
            "params": {"prompt": "按歌词出图: ${workflow-node-x.result.content}"},
        }
    )
    draft["edges"] += [
        {"source": workflow_service.INPUT_ID, "target": "hand-img"},
        {"source": "hand-img", "target": workflow_service.OUTPUT_ID},
    ]
    workflow_service.save_draft(user, None, draft, expected_revision=current["revision"])

    result = asyncio.run(workflow_service.run_workflow(user, None))
    node = next(n for n in result["draft"]["nodes"] if n["id"] == "hand-img")
    assert result["status"] == "failed"
    assert node["runStatus"] == "failed"
    assert "上游" in (node["error"] or "")


def test_run_records_env_snapshot(real_db):
    """C15：一次run入库时必须带上当时环境（模型/prompt hash/能力清单）快照。"""
    user = 906
    _create_user(user)
    _add_lyrics_chain(user)
    submission = submit_workflow_run(user, None)
    run = workflow_run_repo.get(user, submission["run_id"])
    env = run["env_snapshot"]
    assert env["chat_model"]
    assert env["capabilities"] == ["image.generate", "lyrics.generate", "video.generate"]
    assert len(env["prompt_sha256"]) == 16
    assert "30_workflow_rules.md" in env["prompt_sections"]


def test_add_chain_allowed_during_run_via_cas(real_db):
    """运行期间允许继续加链（生命线续命），写入走 CAS，冲突重读重试。

    防的是丢更新（丢节点事故），不是禁止操作本身。"""
    user = 905
    _create_user(user)
    _add_lyrics_chain(user)
    submission = submit_workflow_run(user, None)  # run queued = active

    # 运行期间新加一条独立链：不拒绝，两条链并存、互不接线
    workflow_service.mark_canvas_read(user, None)
    added = workflow_service.add_workflow_chains(
        user, None, [{"template_id": "image_only", "slots": {"prompt": "出图"}}]
    )
    caps = [n.get("capability_id") for n in added["draft"]["nodes"]]
    assert "lyrics.generate" in caps and "image.generate" in caps
    assert [e["chain_no"] for e in added["draft"]["chains"]] == [1, 2]
    _ = submission  # 现场观众：queued 运行仍未结束，不妨碍上面的搭建


def test_usage_ledger_insert_roundtrip(real_db):
    """用量账本占位符/列清单一致性：曾在生产上 '13 values for 12 columns' 静默丢账。"""
    from repositories import usage_repo

    _create_user(904)
    row = usage_repo.insert(
        904, None, "lyrics.generate", "workflow", "glm-4-flash",
        10, 20, 30, "succeeded", None, 12.5, "2026-09-16T09:00:00",
    )
    assert row["prompt_tokens"] == 10
    items = usage_repo.list_for_user(904)
    assert len(items) == 1 and items[0]["capability_id"] == "lyrics.generate"


def test_model_invented_ref_shape_substituted_when_wired(real_db, monkeypatch):
    """模型/用户自造的上游引用写法（${workflow-node-x.result.content}）在参数解析时
    一律按上游引用处理：上游有线且有产出时必须替换干净，“${” 不能残留进上游 API。"""
    from schemas.capability import CapabilityResult

    user = 903
    _create_user(user)
    workflow_service.mark_canvas_read(user, None)
    current = workflow_service.get_draft(user, None)
    draft = copy.deepcopy(current["draft"])
    draft["nodes"] += [
        {
            "id": "hand-lyr",
            "kind": "tool",
            "capability_id": "lyrics.generate",
            "name": "歌词生成",
            "params": {"theme": "t"},
        },
        {
            "id": "hand-img",
            "kind": "tool",
            "capability_id": "image.generate",
            "name": "图像生成",
            "params": {"prompt": "按歌词出图: ${workflow-node-x.result.content}"},
        },
    ]
    draft["edges"] += [
        {"source": workflow_service.INPUT_ID, "target": "hand-lyr"},
        {"source": "hand-lyr", "target": "hand-img"},
        {"source": "hand-img", "target": workflow_service.OUTPUT_ID},
    ]
    workflow_service.save_draft(user, None, draft, expected_revision=current["revision"])

    captured = {}

    async def fake_capability(capability_id, inputs, *, context):
        captured[capability_id] = inputs
        return CapabilityResult(
            capability_id=capability_id,
            status="succeeded",
            result={"format": "markdown", "content": "歌词正文"},
        )

    monkeypatch.setattr(workflow_service, "run_capability", fake_capability)
    result = asyncio.run(workflow_service.run_workflow(user, None))

    assert result["status"] == "succeeded"
    assert "歌词正文" in captured["image.generate"]["prompt"]
    assert "${" not in captured["image.generate"]["prompt"]
