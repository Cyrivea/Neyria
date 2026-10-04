"""计费核心服务测试 —《计费与商业化方案.md》§7/§12 验收的机器化：

- 预占/结算在并发下不透支、不漏扣、不重复扣款；
- 历史费用引用调用时的价格版本，改价不影响旧账；
- 余额不足在上游调用前杜绝（enforce 模式 runtime 集成）；
- 预占在失败/取消/进程死路径都能被回收（release / sweep）。
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core import database, migrations
from core.database import db_connection
from repositories import billing_repo, usage_repo
from services import billing_service


def now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def billing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", tmp_path / "billing.db")
    migrations.run_migrations()
    with db_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("billing-owner@example.com", "hash"),
        )
        user_id = conn.execute("SELECT id FROM users").fetchone()[0]
    billing_service.add_price_version("test-model", "1.0", "2.0", per_call_credits="0.5")
    return user_id


def _account(user_id: int) -> dict:
    with db_connection() as conn:
        account = billing_repo.get_account(conn, user_id)
    assert account is not None
    return account


def test_migration_creates_billing_tables(billing_db):
    with db_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(migrations.MIGRATIONS)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'credit_%' OR name LIKE 'model_%' OR name LIKE 'subscriptions'"
        )}
    assert {"credit_accounts", "credit_reservations", "credit_ledger", "subscriptions", "model_price_versions"} <= tables


def test_ledger_is_immutable(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    with db_connection() as conn, pytest.raises(Exception, match="不可变流水"):
        conn.execute("UPDATE credit_ledger SET amount_micro=999")
    with db_connection() as conn, pytest.raises(Exception, match="不可变流水"):
        conn.execute("DELETE FROM credit_ledger")


def test_grant_and_balance(billing_db):
    result = billing_service.grant_plan_credits(billing_db, "plus", Decimal("50"), 30)
    balance = billing_service.get_balance(billing_db)
    assert result["credits"] == "50"
    assert balance["plan_credits"] == "50"
    assert balance["prepaid_credits"] == "0"
    assert any(entry["type"] == "subscription_grant" for entry in balance["recent_ledger"])


def test_reserve_settle_releases_over_reserved(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:1", Decimal("5"))
    mid = _account(billing_db)
    assert mid["plan_available_micro"] == 5 * 1_000_000
    assert mid["plan_reserved_micro"] == 5 * 1_000_000

    # 真实用量：prompt 1000 + completion 500，按 1.0/2.0 每千 → 1 + 1 + 0.5 = 2.5 积分
    billed = billing_service.settle("resv:1", "test-model", 1000, 500)
    assert billed == Decimal("2.500000")
    final = _account(billing_db)
    assert final["plan_available_micro"] == 7_500_000  # 10 - 5 + 2.5 退回 - 2.5 扣
    assert final["plan_reserved_micro"] == 0


def test_settle_under_reserved_deducts_extra(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:2", Decimal("0.5"))
    # 真实用量是 5 积分：预占的 0.5 退不回，要再补扣 4.5
    billed = billing_service.settle("resv:2", "test-model", 3000, 1000)
    assert billed == Decimal("5.500000")  # 3×1.0 + 1×2.0 + 0.5 固定每次
    final = _account(billing_db)
    assert final["plan_available_micro"] == 4_500_000


def test_settle_is_idempotent(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:3", Decimal("5"))
    billing_service.settle("resv:3", "test-model", 1000, 500)
    again = billing_service.settle("resv:3", "test-model", 1000, 500)
    assert again == Decimal("2.500000")
    # 只记一次结算
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM credit_ledger WHERE reference_id='resv:3' AND type='usage_release'"
        ).fetchone()
    assert rows[0] == 1
    assert _account(billing_db)["plan_available_micro"] == 7_500_000


def test_settle_unknown_reference_raises(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    with pytest.raises(billing_service.ReservationNotFoundError):
        billing_service.settle("resv:never", "test-model", 0, 0)


def test_insufficient_reject_without_touching_account(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("2"), 30)
    with pytest.raises(billing_service.InsufficientCreditsError):
        billing_service.reserve(billing_db, "resv:poor", Decimal("5"))
    assert _account(billing_db)["plan_available_micro"] == 2_000_000


def test_concurrent_reserve_never_oversells(billing_db):
    """10 线程同时预占：100 积分池、每次 15，应恰好成功 6 个，永不为负。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("100"), 30)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(idx: int) -> None:
        try:
            billing_service.reserve(billing_db, f"resv:race:{idx}", Decimal("15"))
            with lock:
                outcomes.append("ok")
        except billing_service.InsufficientCreditsError:
            with lock:
                outcomes.append("insufficient")

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(attempt, range(10)))

    assert outcomes.count("ok") == 6
    assert outcomes.count("insufficient") == 4
    final = _account(billing_db)
    assert final["plan_available_micro"] == 10_000_000
    assert final["plan_reserved_micro"] == 90_000_000
    with db_connection() as conn:
        total_reserved = conn.execute(
            "SELECT COUNT(*) FROM credit_ledger WHERE type='usage_reserve'"
        ).fetchone()[0]
    assert total_reserved == 6


def test_settle_links_usage_event_with_price_snapshot(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:link", Decimal("3"))
    event = usage_repo.insert(
        billing_db, None, "lyrics.generate", "standalone", "test-model",
        1000, 500, 1500, "succeeded", None, 12.0, now(),
    )
    billed = billing_service.settle("resv:link", "test-model", 1000, 500, usage_event_id=int(event["id"]))
    assert billed == Decimal("2.500000")
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM usage_events WHERE id=?", (event["id"],)).fetchone()
    assert row["billed_credits_micro"] == 2_500_000
    assert row["price_version_id"] == 1  # 首个价格版本


def test_release_returns_everything(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:fail", Decimal("4"))
    billing_service.release("resv:fail", "上游 Provider 超时")
    final = _account(billing_db)
    assert final["plan_available_micro"] == 10_000_000
    assert final["plan_reserved_micro"] == 0
    assert billing_service.get_balance(billing_db)["recent_ledger"][0]["type"] == "usage_release"


def test_sweep_stale_reservations_recovers(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:crash", Decimal("4"))
    # 篡改 created_at 让它看起来是 20 分钟前
    with db_connection() as conn:
        conn.execute(
            "UPDATE credit_reservations SET created_at=? WHERE reference_id=?",
            ((datetime.now(UTC) - timedelta(minutes=20)).isoformat(), "resv:crash"),
        )
    assert billing_service.sweep_stale_reservations(600) == 1
    assert _account(billing_db)["plan_available_micro"] == 10_000_000


def test_get_balance_lazy_sweeps_stale_reservations(billing_db):
    """I11 副保险：worker 不在场时，读余额也要顺带回收僵尸预占（默认阈 900s，backdate 20 分钟必中）。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:lazy", Decimal("4"))
    with db_connection() as conn:
        conn.execute(
            "UPDATE credit_reservations SET created_at=? WHERE reference_id=?",
            ((datetime.now(UTC) - timedelta(minutes=20)).isoformat(), "resv:lazy"),
        )
    balance = billing_service.get_balance(billing_db)
    assert balance["plan_credits"] == "10"
    with db_connection() as conn:
        row = conn.execute(
            "SELECT status FROM credit_reservations WHERE reference_id='resv:lazy'"
        ).fetchone()
    assert row[0] == "released"


def test_settle_after_sweep_release_still_charges(billing_db):
    """I11b 竞态：sweep 先于 settle 退回预占，生成随后成功——必须从可用余额兜底直扣，
    不许白送用户一次生成还无人知晓。幂等重放值也必须等于同一次实收。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:race", Decimal("4"))
    billing_service.release("resv:race", reason="模拟 sweep 回收")

    billed = billing_service.settle("resv:race", "test-model", 1000, 1000)
    assert billed == Decimal("3.5")
    account = _account(billing_db)
    assert account["plan_available_micro"] == 6_500_000  # 10 - 3.5
    assert account["plan_reserved_micro"] == 0
    # 幂等重放：返回同一实收且绝不再扣一次
    assert billing_service.settle("resv:race", "test-model", 1000, 1000) == Decimal("3.5")
    assert _account(billing_db)["plan_available_micro"] == 6_500_000


def test_settle_after_release_with_insufficient_balance_alerts(billing_db, caplog):
    """I11b 竞态 + 余额不足：可回收的都被回收完了、用户期间又花光了——不扣负，但欠费告警必须留病。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("1"), 30)
    billing_service.reserve(billing_db, "resv:race-short", Decimal("0.4"))
    billing_service.release("resv:race-short", reason="模拟 sweep 回收")
    # 期间用户把 1 积分花光
    billing_service.reserve(billing_db, "resv:drain", Decimal("1"))
    billing_service.settle("resv:drain", "test-model", 0, 0)  # 实收 0.5，退回 0.5
    billing_service.reserve(billing_db, "resv:drain2", Decimal("0.5"))
    billing_service.settle("resv:drain2", "test-model", 0, 0)  # 实收 0.5，清零
    assert _account(billing_db)["plan_available_micro"] == 0

    with caplog.at_level("WARNING", logger="billing"):
        billed = billing_service.settle("resv:race-short", "test-model", 1000, 1000)
    assert billed == Decimal("3.5")
    assert _account(billing_db)["plan_available_micro"] == 0  # 一分不扣负
    alerts = [r for r in caplog.records if getattr(r, "evt", "") == "billing_settle_after_release"]
    assert len(alerts) == 1
    assert alerts[0].undercharged_micro == 3_500_000


def test_expire_subscriptions_zeroes_plan_credits_once(billing_db):
    billing_service.grant_plan_credits(billing_db, "test", Decimal("8"), 30)
    with db_connection() as conn:
        conn.execute(
            "UPDATE subscriptions SET period_end=?",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(),),
        )
    assert billing_service.expire_subscriptions() == 1
    assert _account(billing_db)["plan_available_micro"] == 0
    # 再跑一遍不再重复清零（幂等）
    assert billing_service.expire_subscriptions() == 0
    ledger = billing_service.get_balance(billing_db)["recent_ledger"]
    expires = [entry for entry in ledger if entry["type"] == "expiration"]
    assert len(expires) == 1
    assert expires[0]["amount_credits"] == "-8"


def test_plan_priority_plan_bucket_first(billing_db):
    """混合余额时先烧套餐，不够再动预付（方案 §2.2）。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("1.5"), 30)
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        billing_repo.ensure_account(conn, billing_db, now())
        account = billing_repo.get_account(conn, billing_db)
        assert account is not None
        billing_repo.update_account(
            conn, billing_db,
            plan_available=account["plan_available_micro"],
            plan_reserved=account["plan_reserved_micro"],
            prepaid_available=3_000_000,
            prepaid_reserved=0,
            now=now(),
        )
    billing_service.reserve(billing_db, "resv:mixed", Decimal("2"))
    account = _account(billing_db)
    assert account["plan_available_micro"] == 0
    assert account["plan_reserved_micro"] == 1_500_000
    assert account["prepaid_available_micro"] == 2_500_000
    assert account["prepaid_reserved_micro"] == 500_000


# ===== I6：预付余额充值与超额扣减 =====


def test_top_up_then_overflow_burns_prepaid(billing_db):
    """I6 验收：套餐用尽后按量扣预付；充值走正式入口而非手戳账户。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("1"), 30)
    result = billing_service.top_up_prepaid(billing_db, Decimal("5"), "order:i6")
    assert result == {"duplicate": False, "prepaid_credits": "5"}

    # 预占3：套餐桶全部 1 + 预付桶 2
    billing_service.reserve(billing_db, "resv:i6", Decimal("3"))
    # 实销 2.5（1000入×1.0/1k + 500出×2.0/1k + 0.5/次）：套餐出 1，预付出 1.5，退 0.5 回预付
    billed = billing_service.settle("resv:i6", "test-model", 1000, 500)
    assert billed == Decimal("2.5")

    account = _account(billing_db)
    assert account["plan_available_micro"] == 0
    assert account["prepaid_available_micro"] == 3_500_000
    assert account["plan_reserved_micro"] == 0
    assert account["prepaid_reserved_micro"] == 0


def test_top_up_is_idempotent_on_order_reference(billing_db):
    """支付回调重发同一订单号只入账一次。"""
    billing_service.top_up_prepaid(billing_db, Decimal("5"), "order:dup")
    replay = billing_service.top_up_prepaid(billing_db, Decimal("5"), "order:dup")
    assert replay["duplicate"] is True
    assert replay["prepaid_credits"] == "5"
    with db_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM credit_ledger WHERE type='top_up' AND reference_id='order:dup'"
        ).fetchone()[0]
    assert count == 1


def test_top_up_rejects_non_positive(billing_db):
    with pytest.raises(billing_service.BillingError):
        billing_service.top_up_prepaid(billing_db, Decimal("0"), "order:zero")
    with pytest.raises(billing_service.BillingError):
        billing_service.top_up_prepaid(billing_db, Decimal("-1"), "order:neg")


def test_duplicate_reserve_reference_rejected(billing_db):
    """I12：同一幂等键重复预占必须显式拒绝——静默吞掉会让平台替用户多付出一次 Provider 成本。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.reserve(billing_db, "resv:dup", Decimal("4"))
    with pytest.raises(billing_service.DuplicateReservationError):
        billing_service.reserve(billing_db, "resv:dup", Decimal("4"))
    assert _account(billing_db)["plan_available_micro"] == 6_000_000  # 只扣一次


def test_both_buckets_insufficient_rejected(billing_db):
    """两桶加起来不够也必须拒绝，且两桶余额分文不动（不允许负余额）。"""
    billing_service.grant_plan_credits(billing_db, "test", Decimal("1"), 30)
    billing_service.top_up_prepaid(billing_db, Decimal("1"), "order:small")
    with pytest.raises(billing_service.InsufficientCreditsError):
        billing_service.reserve(billing_db, "resv:toobig", Decimal("3"))
    account = _account(billing_db)
    assert account["plan_available_micro"] == 1_000_000
    assert account["prepaid_available_micro"] == 1_000_000


# ===== Runtime 集成：enforce 拒上游 / track 只记账 =====

@pytest.fixture
def staging_entry(monkeypatch):
    """注入一个不走真实模型的替身能力，间谍化 executor 确认调用顺序。"""
    calls: list[str] = []

    def executor(inputs):
        calls.append("executor")
        return {"content": "ok", "usage": {"model": "test-model", "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}

    from dataclasses import replace

    from services.capabilities import runtime

    stub = replace(
        runtime.CAPABILITY_REGISTRY["lyrics.generate"],
        executor=executor,
        billing_model=lambda: "test-model",
        billing_estimate=lambda: Decimal("1"),
    )
    monkeypatch.setitem(runtime.CAPABILITY_REGISTRY, "lyrics.generate", stub)
    return calls


def test_runtime_enforce_blocks_insufficient_before_executor(billing_db, staging_entry, monkeypatch):
    monkeypatch.setattr("core.config.BILLING_MODE", "enforce")
    import asyncio

    from services.capabilities import runtime

    async def exec_run():
        return await runtime.run_capability(
            "lyrics.generate",
            {"theme": "moon"},
            context=runtime.CapabilityContext(user_id=billing_db, project_id=None, source="standalone"),
        )

    # 先不充值：应被预占拦截，executor 不允许被触碰
    result = asyncio.run(exec_run())
    assert result.status == "failed"
    assert "积分不足" in (result.error or "")
    assert staging_entry == []

    # 充值后放行，executor 被调一次，结算后套餐余额正确
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    result = asyncio.run(exec_run())
    assert result.status == "succeeded"
    assert staging_entry == ["executor"]
    # billed = 100/1000×1.0 + 50/1000×2.0 + 0.5 每次调用 = 0.7 积分；预占 1，差额 0.3 退回，净扣 0.7
    account = _account(billing_db)
    assert account["plan_reserved_micro"] == 0
    assert account["plan_available_micro"] == 9_300_000


def test_runtime_track_mode_logs_but_never_blocks(billing_db, staging_entry, monkeypatch):
    monkeypatch.setattr("core.config.BILLING_MODE", "track")
    import asyncio

    from services.capabilities import runtime

    result = asyncio.run(
        runtime.run_capability(
            "lyrics.generate",
            {"theme": "moon"},
            context=runtime.CapabilityContext(user_id=billing_db, project_id=None, source="standalone"),
        )
    )
    assert result.status == "succeeded"
    assert staging_entry == ["executor"]
    # track 不走预占（欠费也不拦）：账本没扣，甚至账户行都没建
    balance = billing_service.get_balance(billing_db)
    assert balance["plan_credits"] == "0"
    assert balance["recent_ledger"] == []


# ===== I12：客户端幂等键防双击双重生成 =====


def _run_standalone(user_id: int, monkeypatch, key: str):
    import asyncio

    from services.capabilities import runtime

    return asyncio.run(
        runtime.run_capability(
            "lyrics.generate",
            {"theme": "moon"},
            context=runtime.CapabilityContext(user_id=user_id, project_id=None, source="standalone"),
            idempotency_key=key,
        )
    )


def test_runtime_duplicate_idempotency_key_rejected(billing_db, staging_entry, monkeypatch):
    """同一幂等键双击/断网重试：只执行一次生成、只动一次钱，第二次显式拒绝。"""
    monkeypatch.setattr("core.config.BILLING_MODE", "enforce")
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)

    first = _run_standalone(billing_db, monkeypatch, "fixed-key-1234")
    assert first.status == "succeeded"
    assert staging_entry == ["executor"]

    with pytest.raises(billing_service.DuplicateReservationError):
        _run_standalone(billing_db, monkeypatch, "fixed-key-1234")
    assert staging_entry == ["executor"]  # 第二次没摸到生成器
    assert _account(billing_db)["plan_available_micro"] == 9_300_000  # 只扣一次 0.7

    # 不同 key = 用户明确再生成一次，正常放行正常收费（防误判邻点）
    second = _run_standalone(billing_db, monkeypatch, "fixed-key-5678")
    assert second.status == "succeeded"
    assert staging_entry == ["executor", "executor"]
    assert _account(billing_db)["plan_available_micro"] == 8_600_000


def test_runtime_concurrent_duplicate_key_single_execution(billing_db, staging_entry, monkeypatch):
    """同一幂等键并发双击（竞态窗口）：BEGIN IMMEDIATE 串行后只有一个赢家。"""
    monkeypatch.setattr("core.config.BILLING_MODE", "enforce")
    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)

    outcomes: list[object] = []

    def run():
        try:
            outcomes.append(_run_standalone(billing_db, monkeypatch, "fixed-race-key"))
        except billing_service.DuplicateReservationError as exc:
            outcomes.append(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        t1 = pool.submit(run)
        t2 = pool.submit(run)
        t1.result()
        t2.result()

    duplicates = [o for o in outcomes if isinstance(o, billing_service.DuplicateReservationError)]
    successes = [o for o in outcomes if not isinstance(o, Exception) and o.status == "succeeded"]
    assert len(successes) == 1
    assert len(duplicates) == 1
    assert staging_entry == ["executor"]
    assert _account(billing_db)["plan_available_micro"] == 9_300_000


# ===== 对账脚本（Spec §2.5，scripts/reconcile_billing.py）=====


def test_reconcile_passes_after_full_lifecycle(billing_db):
    """健康账：发放→预占→结算→退回→冻结，全流程后三条不变量必须成立。"""
    from scripts import reconcile_billing

    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    billing_service.top_up_prepaid(billing_db, Decimal("5"), "order:recon")
    billing_service.reserve(billing_db, "resv:recon1", Decimal("2"))
    billing_service.settle("resv:recon1", "test-model", 1000, 500)
    billing_service.reserve(billing_db, "resv:recon2", Decimal("1"))
    billing_service.release("resv:recon2")
    billing_service.reserve(billing_db, "resv:recon3", Decimal("1.5"))  # 留着 open
    assert reconcile_billing.main() == 0


def test_balance_includes_active_plan_snapshot(billing_db):
    """I8 尾活：余额接口必须出当前生效套餐快照（进度条的数据源），过期套餐不出。"""
    # 无套餐时快照亮出为 None
    assert billing_service.get_balance(billing_db)["plan_snapshot"] is None

    billing_service.grant_plan_credits(billing_db, "creator", Decimal("100"), 30)
    snap = billing_service.get_balance(billing_db)["plan_snapshot"]
    assert snap is not None
    assert snap["plan"] == "creator"
    assert snap["included_credits"] == "100"
    assert snap["period_end"] > datetime.now(UTC).isoformat()

    # 套餐到期后（凭 expire 清扫翻过状态）不再出快照
    with db_connection() as conn:
        conn.execute(
            "UPDATE subscriptions SET period_end=?",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(),),
        )
    assert billing_service.get_balance(billing_db)["plan_snapshot"] is None


def test_usage_summary_aggregates_by_capability(billing_db):
    """usage-summary 聚合：30 天窗口内按能力分流，金额取已结算的 billed_credits。"""
    from repositories import usage_repo

    since = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    future = datetime.now(UTC).isoformat()
    usage_repo.insert(billing_db, None, "lyrics.generate", "standalone", "test-model", 100, 50, 150, "succeeded", None, 100, future)
    usage_repo.insert(billing_db, None, "lyrics.generate", "workflow", "test-model", 200, 100, 300, "failed", "x", 200, future)
    usage_repo.insert(billing_db, None, "image.generate", "standalone", "img-m", 0, 0, 0, "succeeded", None, 300, future)
    # 一个月前的老数据必须被窗口排除
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    usage_repo.insert(billing_db, None, "lyrics.generate", "standalone", "test-model", 10, 10, 20, "succeeded", None, 50, old)

    rows = usage_repo.summarize_for_user_since(billing_db, since)
    by_cap = {r["capability_id"]: r for r in rows}
    assert set(by_cap) == {"lyrics.generate", "image.generate"}
    assert by_cap["lyrics.generate"]["calls"] == 2
    assert by_cap["lyrics.generate"]["succeeded"] == 1
    assert by_cap["image.generate"]["calls"] == 1


def test_reconcile_catches_unauthorized_account_mutation(billing_db):
    """手贱/bug 直接改账户表绕过流水——对账必须报警而非静默。"""
    from scripts import reconcile_billing

    billing_service.grant_plan_credits(billing_db, "test", Decimal("10"), 30)
    with db_connection() as conn:
        conn.execute("UPDATE credit_accounts SET plan_available_micro = plan_available_micro - 12345")
    assert reconcile_billing.main() == 1
