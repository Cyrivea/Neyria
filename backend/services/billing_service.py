"""计费核心服务：订阅 entitlement、价格版本、额度预占/结算。

与《计费与商业化方案.md》对应关系：
- I4 订阅 entitlement：``grant_plan_credits``（管理员/测试发放套餐额度）；
- I3 价格版本：``add_price_version`` + ``compute_billed_credits``，历史账引用
  调用发生时的版本 id，管理员事后改价不影响旧账；
- I5 预占/结算：``reserve`` → ``settle`` / ``release``；
  全程 BEGIN IMMEDIATE + 账户 CHECK 非负约束，并发下不透支、不重复扣款。

数量单位约定（全代码唯一约定点）：
- 对外接口一律用 ``Decimal`` 积分；
- 库内一律存 **micro-积分整数**（1 积分 = 1_000_000 micro）——
  SQLite 没有定点类型，二进制浮点禁止进账务（方案 §4）。

扣减顺序（方案 §2.2）：套餐额度优先，用完才动预付余额。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal
from typing import Any

from core import config
from core.database import db_connection
from repositories import billing_repo

logger = logging.getLogger("billing")

MICRO_PER_CREDIT = 1_000_000


class BillingError(Exception):
    """计费服务可预期错误基类。"""


class InsufficientCreditsError(BillingError):
    """套餐 + 预付不足以覆盖预占。调用方应在接触上游 Provider 之前拒绝。"""


class DuplicateReservationError(BillingError):
    """同一幂等键重复预占：调用方必须视为重复提交，不得再执行一次生成。"""


class ReservationNotFoundError(BillingError):
    """找不到对应幂等键的预占记录（结算/释放一个从未预占的 key）。"""


class PriceNotConfiguredError(BillingError):
    """该上游模型在当前时点没有任何生效价格版本。"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def credits_to_micro(credits: Decimal) -> int:
    """Decimal 积分 → micro 整数。调用方应保证 credits 非负且数值合理。"""
    if credits < 0:
        raise BillingError("积分数量不能为负")
    return int((credits * MICRO_PER_CREDIT).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def micro_to_credits(micro: int) -> Decimal:
    """micro 整数 → Decimal 积分，剔掉尾零（对外展示 "5" 而非 "5.000000"）。

    注意 `Decimal.normalize()` 的坑：整十倍数会变科学计数法（50 → 5E+1），
    所以整数值要再 quantize 回整数形态。
    """
    value = (Decimal(micro) / MICRO_PER_CREDIT).normalize()
    if value == value.to_integral_value():
        return value.quantize(Decimal("1"))
    return value


def add_price_version(
    model_id: str,
    input_price_per_1k: Decimal | str,
    output_price_per_1k: Decimal | str,
    *,
    cache_price_per_1k: Decimal | str = Decimal("0"),
    reasoning_price_per_1k: Decimal | str = Decimal("0"),
    per_call_credits: Decimal | str = Decimal("0"),
    credit_multiplier: Decimal | str = Decimal("1"),
    effective_from: str | None = None,
) -> int:
    """登记一个价格版本。价格只可追加新版本，历史版本永远不改——这是「改价不影响旧账」的根基。"""
    now = _utcnow()

    def canon(value: Decimal | str) -> str:
        v = Decimal(value)
        if v < 0:
            raise BillingError("价格不能为负")
        return str(v.normalize() if v == v.normalize() else v)

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return billing_repo.insert_price_version(
            conn,
            model_id,
            canon(input_price_per_1k),
            canon(output_price_per_1k),
            cache_price_per_1k=canon(cache_price_per_1k),
            reasoning_price_per_1k=canon(reasoning_price_per_1k),
            per_call_credits=canon(per_call_credits),
            credit_multiplier=canon(credit_multiplier),
            effective_from=effective_from or now,
            created_at=now,
        )


def compute_billed_credits(
    price: dict[str, Any],
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> Decimal:
    """按价格版本把真实 token 用量折成应收积分（向上取整到 micro 精度，保证不低收）。"""
    thousand = Decimal("1000")
    token_cost = (
        Decimal(prompt_tokens) * Decimal(price["input_price_per_1k"])
        + Decimal(completion_tokens) * Decimal(price["output_price_per_1k"])
        + Decimal(cached_tokens) * Decimal(price["cache_price_per_1k"])
        + Decimal(reasoning_tokens) * Decimal(price["reasoning_price_per_1k"])
    ) / thousand
    total = (token_cost + Decimal(price["per_call_credits"])) * Decimal(price["credit_multiplier"])
    return total.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def grant_plan_credits(
    user_id: int,
    plan: str,
    credits: Decimal,
    period_days: int,
) -> dict[str, Any]:
    """向用户发放一个套餐周期（管理员/测试套餐的模拟发放，真实订单后置）。"""
    micro = credits_to_micro(credits)
    now = _utcnow()
    period_end = (datetime.now(UTC) + timedelta(days=period_days)).isoformat()

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        billing_repo.ensure_account(conn, user_id, now)
        subscription_id = billing_repo.insert_subscription(
            conn, user_id, plan, "active", now, period_end, micro, now
        )
        account = billing_repo.get_account(conn, user_id)
        assert account is not None
        billing_repo.update_account(
            conn,
            user_id,
            plan_available=account["plan_available_micro"] + micro,
            plan_reserved=account["plan_reserved_micro"],
            prepaid_available=account["prepaid_available_micro"],
            prepaid_reserved=account["prepaid_reserved_micro"],
            now=now,
        )
        account = billing_repo.get_account(conn, user_id)
        assert account is not None
        billing_repo.insert_ledger(
            conn,
            user_id,
            micro,
            account["plan_available_micro"],
            account["prepaid_available_micro"],
            "subscription_grant",
            f"sub:{subscription_id}:grant",
            f"套餐 {plan} 发放 {credits} 积分",
            now,
        )
    logger.info(
        "套餐额度已发放",
        extra={
            "evt": "subscription_granted",
            "user_id": user_id,
            "plan": plan,
            "credits": str(credits),
            "subscription_id": subscription_id,
        },
    )
    return {"subscription_id": subscription_id, "credits": str(credits), "period_end": period_end}


def top_up_prepaid(user_id: int, credits: Decimal, reference_id: str) -> dict[str, Any]:
    """预付余额充值入账（阶段 1 为模拟订单，真实支付回调后置）。

    幂等：同一 reference_id（将来对应支付订单号，回调可能重发）只入账一次，
    靠 ledger (type, reference_id) 唯一索引 + 事务内先查后写双重兑底。
    套餐额度用尽后自动改扣预付余额的顺序逻辑在 ``_reserve_split``，
    本函数只负责往预付桶里加水。
    """
    if credits <= 0:
        raise BillingError("充值金额必须为正数")
    micro = credits_to_micro(credits)
    now = _utcnow()

    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if billing_repo.ledger_entry_exists(conn, "top_up", reference_id):
            # 幂等重放：同一订单号已入过账，直接返回现状，不重复加钱
            account = billing_repo.get_account(conn, user_id)
            assert account is not None
            return {
                "duplicate": True,
                "prepaid_credits": str(micro_to_credits(account["prepaid_available_micro"])),
            }
        billing_repo.ensure_account(conn, user_id, now)
        account = billing_repo.get_account(conn, user_id)
        assert account is not None
        billing_repo.update_account(
            conn,
            user_id,
            plan_available=account["plan_available_micro"],
            plan_reserved=account["plan_reserved_micro"],
            prepaid_available=account["prepaid_available_micro"] + micro,
            prepaid_reserved=account["prepaid_reserved_micro"],
            now=now,
        )
        account = billing_repo.get_account(conn, user_id)
        assert account is not None
        billing_repo.insert_ledger(
            conn,
            user_id,
            micro,
            account["plan_available_micro"],
            account["prepaid_available_micro"],
            "top_up",
            reference_id,
            f"预付余额充值 {credits} 积分（模拟订单）",
            now,
        )
    logger.info(
        "预付余额已入账",
        extra={
            "evt": "prepaid_topped_up",
            "user_id": user_id,
            "credits": str(credits),
            "reference_id": reference_id,
        },
    )
    return {
        "duplicate": False,
        "prepaid_credits": str(micro_to_credits(account["prepaid_available_micro"])),
    }


def expire_subscriptions(now: str | None = None) -> int:
    """周期到点清零套餐额度（一次性记账，重复调用幂等）。

    方案 §13 未冻结「到期是否清零」，采用最常见的保守默认：清零。
    ledger 的一条 expiration 记同一时间点清零的总量，幂等键 sub:{id}:expire。
    """
    now = now or _utcnow()
    expired = 0
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        actives = [
            sub
            for sub in billing_repo.list_subscriptions(conn)
            if sub["status"] == "active" and sub["period_end"] <= now
        ]
        for sub in actives:
            billing_repo.mark_subscription_status(conn, sub["id"], "expired", now)
            expired += 1
            reference = f"sub:{sub['id']}:expire"
            if billing_repo.ledger_entry_exists(conn, "expiration", reference):
                continue  # 已清过，幂等返回
            account = billing_repo.get_account(conn, sub["user_id"])
            if account is None:
                continue
            plan_total = account["plan_available_micro"] + account["plan_reserved_micro"]
            if plan_total <= 0:
                continue
            billing_repo.update_account(
                conn,
                sub["user_id"],
                plan_available=0,
                plan_reserved=0,
                prepaid_available=account["prepaid_available_micro"],
                prepaid_reserved=account["prepaid_reserved_micro"],
                now=now,
            )
            billing_repo.insert_ledger(
                conn,
                sub["user_id"],
                -plan_total,
                0,
                account["prepaid_available_micro"],
                "expiration",
                reference,
                f"套餐 {sub['plan']} 到期清零 {micro_to_credits(plan_total)} 积分",
                now,
            )
    return expired


def get_balance(user_id: int, *, now: str | None = None) -> dict[str, Any]:
    """读取用户积分视图：先顺手完成到期清零/超时预占回收，再返回两桶可用/预占。

    惰性 sweep 是 I11 的副保险：主保险是 Worker 每 BILLING_SWEEP_INTERVAL_SECONDS 主动扫；
    worker 没跑（比如只起了 uvicorn 的环境）时，用户打开计费面板也能顺带回收僵尸预占。
    两处并发碰同一笔预占时 release 内 BEGIN IMMEDIATE 串行 + 非 open 静默返回，天然幂等。

    I8 补充：附带当前生效套餐快照（档位/总额度/到期日），供计费页进度条算「已用占比」——
    前端不猜套餐总额，一切数字以后端账本为准。
    """
    now = now or _utcnow()
    expire_subscriptions(now)
    try:
        sweep_stale_reservations(config.BILLING_RESERVATION_STALE_SECONDS)
    except Exception as exc:
        # 读余额不能被打胜 90% 的清理任务拒绝；失败只记日志，下一轮 worker 轮询会补上
        logger.warning("余额查询顺带清扫预占失败：%s", exc, extra={"evt": "billing_lazy_sweep_failed"})
    with db_connection() as conn:
        conn.row_factory = sqlite3.Row
        account = billing_repo.get_account(conn, user_id)
        ledger = billing_repo.list_ledger_for_user(conn, user_id, 20)
        active_sub = next(
            (
                sub
                for sub in billing_repo.list_subscriptions(conn, user_id)
                if sub["status"] == "active" and sub["period_end"] > now
            ),
            None,
        )
    account = account or {
        "plan_available_micro": 0,
        "plan_reserved_micro": 0,
        "prepaid_available_micro": 0,
        "prepaid_reserved_micro": 0,
    }
    plan_snapshot = None
    if active_sub is not None:
        plan_snapshot = {
            "plan": active_sub["plan"],
            "included_credits": str(micro_to_credits(active_sub["included_credits_micro"])),
            "period_end": active_sub["period_end"],
        }
    return {
        "plan_credits": str(micro_to_credits(account["plan_available_micro"])),
        "prepaid_credits": str(micro_to_credits(account["prepaid_available_micro"])),
        "plan_reserved_credits": str(micro_to_credits(account["plan_reserved_micro"])),
        "prepaid_reserved_credits": str(micro_to_credits(account["prepaid_reserved_micro"])),
        "plan_snapshot": plan_snapshot,
        "recent_ledger": [
            {
                "type": entry["type"],
                "amount_credits": str(micro_to_credits(entry["amount_micro"])),
                "plan_balance_after": str(micro_to_credits(entry["plan_balance_after_micro"])),
                "prepaid_balance_after": str(micro_to_credits(entry["prepaid_balance_after_micro"])),
                "description": entry["description"],
                "created_at": entry["created_at"],
            }
            for entry in ledger
        ],
    }


def _reserve_split(account: dict[str, Any], amount_micro: int) -> tuple[int, int]:
    """按比例从套餐桶先扣；套餐不够再从预付桶补。"""
    from_plan = min(account["plan_available_micro"], amount_micro)
    return from_plan, amount_micro - from_plan


def reserve(user_id: int, reference_id: str, estimated: Decimal) -> int:
    """调用上游 Provider 前的原子预占：额度不够立即拒绝，绝不欠费。

    同一 reference_id 重复预占 → DuplicateReservationError：调用方必须保证引用键唯一，
    冲突意味着同一次生成被重复提交（双击/重试），此时静默执行会让平台为同一用户多付一次
    Provider 成本（用户只被扣一次但实付了两次）——重复提交必须显式拒绝而非吞掉。
    并发安全靠三重防线：BEGIN IMMEDIATE 写串行化 + 账户 CHECK 非负约束 +
    流水唯一索引；线程打爆也不会出现负额或双扣。
    """
    amount_micro = credits_to_micro(estimated)
    now = _utcnow()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        billing_repo.ensure_account(conn, user_id, now)
        existing = billing_repo.get_reservation_by_reference(conn, reference_id)
        if existing is not None:
            raise DuplicateReservationError(f"幂等键 {reference_id} 已存在预占，拒绝重复提交")
        account = billing_repo.get_account(conn, user_id)
        assert account is not None
        from_plan, from_prepaid = _reserve_split(account, amount_micro)
        if amount_micro > account["plan_available_micro"] + account["prepaid_available_micro"]:
            raise InsufficientCreditsError(
                f"积分不足：需要 {estimated}，套餐可用 {micro_to_credits(account['plan_available_micro'])}"
                f"，预付可用 {micro_to_credits(account['prepaid_available_micro'])}"
            )
        billing_repo.update_account(
            conn,
            user_id,
            plan_available=account["plan_available_micro"] - from_plan,
            plan_reserved=account["plan_reserved_micro"] + from_plan,
            prepaid_available=account["prepaid_available_micro"] - from_prepaid,
            prepaid_reserved=account["prepaid_reserved_micro"] + from_prepaid,
            now=now,
        )
        reservation_id = billing_repo.insert_reservation(
            conn, user_id, reference_id, from_plan, from_prepaid, now
        )
        billing_repo.insert_ledger(
            conn,
            user_id,
            -amount_micro,
            account["plan_available_micro"] - from_plan,
            account["prepaid_available_micro"] - from_prepaid,
            "usage_reserve",
            reference_id,
            f"调用预占 {estimated} 积分",
            now,
        )
    return reservation_id


def settle(
    reference_id: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    usage_event_id: int | None = None,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> Decimal:
    """按真实用量结算一笔预占。幂等：同一 reference 重复 settle 返回同一结果。

    结算时若实际用量超过预占，先吃剩余可用额度；仍不够就记负差额告警——
    账户不会被扣负（CHECK 约束），但欠费会记入日志供对账与风控回溯。
    """
    now = _utcnow()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        reservation = billing_repo.get_reservation_by_reference(conn, reference_id)
        if reservation is None or reservation["status"] != "open":
            if reservation is None:
                raise ReservationNotFoundError(f"幂等键 {reference_id} 未找到任何预占记录")
            if reservation["status"] == "released":
                # I11b 竞态兜底：预占已被 sweep/失败路径退回，生成却成功了——
                # 平台已替用户垫付 Provider 成本，必须从可用余额直扣，不许吃暗亏。
                return _settle_after_release(
                    conn,
                    reservation,
                    model_id,
                    prompt_tokens,
                    completion_tokens,
                    cached_tokens=cached_tokens,
                    reasoning_tokens=reasoning_tokens,
                    usage_event_id=usage_event_id,
                    now=now,
                )
            if reservation["status"] != "settled":
                raise ReservationNotFoundError(f"幂等键 {reference_id} 未找到已结算记录")
            # 幂等重放：从流水回推上次的实收金额。
            # 记账公式：billed = 预占总额 - 退回之和 - 补扣之和（补扣为负数行）
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN type='usage_release' THEN amount_micro ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN type='usage_settlement' THEN amount_micro ELSE 0 END), 0)
                FROM credit_ledger WHERE reference_id=? AND type IN ('usage_release', 'usage_settlement')
                """,
                (reference_id,),
            ).fetchone()
            released_sum = int(row[0])
            settled_delta_sum = int(row[1])  # 负数 = 补扣
            reserved_total = int(reservation["plan_micro"]) + int(reservation["prepaid_micro"])
            return micro_to_credits(reserved_total - released_sum - settled_delta_sum)

        user_id = int(reservation["user_id"])
        billing_repo.ensure_account(conn, user_id, now)
        account = billing_repo.get_account(conn, user_id)
        assert account is not None

        price = billing_repo.current_price_version(conn, model_id, now)
        if price is None:
            raise PriceNotConfiguredError(f"模型 {model_id} 在 {now} 无生效价格版本")
        billed = compute_billed_credits(
            price,
            prompt_tokens,
            completion_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        billed_micro = credits_to_micro(billed)

        reserved_plan = int(reservation["plan_micro"])
        reserved_prepaid = int(reservation["prepaid_micro"])

        # 先烧预占（套餐桶优先），再补差（仍套餐桶优先），仍不够的部分记风控告警
        charged_plan = min(reserved_plan, billed_micro)
        remaining = billed_micro - charged_plan
        charged_prepaid_reserved = min(reserved_prepaid, remaining)
        remaining -= charged_prepaid_reserved
        extra_from_plan = min(account["plan_available_micro"], remaining)
        remaining -= extra_from_plan
        extra_from_prepaid = min(account["prepaid_available_micro"], remaining)
        undercharged_micro = remaining - extra_from_prepaid

        plan_released = reserved_plan - charged_plan
        prepaid_released = reserved_prepaid - charged_prepaid_reserved

        billing_repo.update_account(
            conn,
            user_id,
            plan_available=account["plan_available_micro"] + plan_released - extra_from_plan,
            plan_reserved=account["plan_reserved_micro"] - reserved_plan,
            prepaid_available=account["prepaid_available_micro"] + prepaid_released - extra_from_prepaid,
            prepaid_reserved=account["prepaid_reserved_micro"] - reserved_prepaid,
            now=now,
        )
        billing_repo.close_reservation(conn, int(reservation["id"]), "settled", now)

        settled_account = billing_repo.get_account(conn, user_id)
        assert settled_account is not None
        # 结算的净差额（相对预占而言）：补扣为正差，退回为负差，各自独立一条流水
        delta_micro = plan_released + prepaid_released - extra_from_plan - extra_from_prepaid
        if delta_micro < 0:
            billing_repo.insert_ledger(
                conn,
                user_id,
                delta_micro,
                settled_account["plan_available_micro"],
                settled_account["prepaid_available_micro"],
                "usage_settlement",
                reference_id,
                f"结算补扣 {micro_to_credits(-delta_micro)} 积分（{model_id}，价版 {price['id']}）",
                now,
            )
        elif delta_micro > 0:
            billing_repo.insert_ledger(
                conn,
                user_id,
                delta_micro,
                settled_account["plan_available_micro"],
                settled_account["prepaid_available_micro"],
                "usage_release",
                reference_id,
                f"结算退回多预占 {micro_to_credits(delta_micro)} 积分（{model_id}，价版 {price['id']}）",
                now,
            )
        else:
            billing_repo.insert_ledger(
                conn,
                user_id,
                0,
                settled_account["plan_available_micro"],
                settled_account["prepaid_available_micro"],
                "usage_settlement",
                reference_id,
                f"结算与预占一致（{model_id}，价版 {price['id']}）",
                now,
            )
        if undercharged_micro > 0:
            logger.warning(
                "实际费用超过余额，发生欠费 %s micro，账户未被扣负，请人工复核",
                undercharged_micro,
                extra={
                    "evt": "billing_undercharged",
                    "user_id": user_id,
                    "reference_id": reference_id,
                    "undercharged_micro": undercharged_micro,
                },
            )
        if usage_event_id is not None:
            billing_repo.link_usage_event_billing(
                conn, usage_event_id, int(price["id"]), billed_micro
            )
    return billed


def _settle_after_release(
    conn: sqlite3.Connection,
    reservation: dict[str, Any],
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cached_tokens: int,
    reasoning_tokens: int,
    usage_event_id: int | None,
    now: str,
) -> Decimal:
    """I11b 竞态兜底：预占被 sweep/release 退回后，迟到的 settled 请求改从可用余额直扣。

    余额不够的部分走统一 `billing_undercharged` 告警，账户不被扣负；
    顺手把 reservation 状态纠正为 settled，保持历史诚实（reservation 不是 ledger，允许改状态）。
    """
    user_id = int(reservation["user_id"])
    billing_repo.ensure_account(conn, user_id, now)
    account = billing_repo.get_account(conn, user_id)
    assert account is not None
    price = billing_repo.current_price_version(conn, model_id, now)
    if price is None:
        raise PriceNotConfiguredError(f"模型 {model_id} 在 {now} 无生效价格版本")
    billed = compute_billed_credits(
        price,
        prompt_tokens,
        completion_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )
    billed_micro = credits_to_micro(billed)
    from_plan = min(account["plan_available_micro"], billed_micro)
    from_prepaid = min(account["prepaid_available_micro"], billed_micro - from_plan)
    undercharged_micro = billed_micro - from_plan - from_prepaid
    charged_micro = from_plan + from_prepaid
    reference_id = str(reservation["reference_id"])
    billing_repo.update_account(
        conn,
        user_id,
        plan_available=account["plan_available_micro"] - from_plan,
        plan_reserved=account["plan_reserved_micro"],
        prepaid_available=account["prepaid_available_micro"] - from_prepaid,
        prepaid_reserved=account["prepaid_reserved_micro"],
        now=now,
    )
    billing_repo.close_reservation(conn, int(reservation["id"]), "settled", now)
    settled_account = billing_repo.get_account(conn, user_id)
    assert settled_account is not None
    billing_repo.insert_ledger(
        conn,
        user_id,
        -charged_micro,
        settled_account["plan_available_micro"],
        settled_account["prepaid_available_micro"],
        "usage_settlement",
        reference_id,
        f"超时回收后兜底直扣 {micro_to_credits(charged_micro)} 积分（{model_id}，价版 {price['id']}）",
        now,
    )
    logger.warning(
        "结算晚于预占回收，已从可用余额兜底直扣",
        extra={
            "evt": "billing_settle_after_release",
            "user_id": user_id,
            "reference_id": reference_id,
            "charged_micro": charged_micro,
            "undercharged_micro": undercharged_micro,
        },
    )
    if usage_event_id is not None:
        billing_repo.link_usage_event_billing(conn, usage_event_id, int(price["id"]), billed_micro)
    return billed


def release(reference_id: str, reason: str = "") -> None:
    """上游调用未完成 / 失败 / 取消时整笔退回预占。幂等：已关闭的预占原样返回。"""
    now = _utcnow()
    with db_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        reservation = billing_repo.get_reservation_by_reference(conn, reference_id)
        if reservation is None:
            raise ReservationNotFoundError(f"幂等键 {reference_id} 未找到预占记录")
        if reservation["status"] != "open":
            return
        user_id = int(reservation["user_id"])
        billing_repo.ensure_account(conn, user_id, now)
        account = billing_repo.get_account(conn, user_id)
        assert account is not None
        plan_back = int(reservation["plan_micro"])
        prepaid_back = int(reservation["prepaid_micro"])
        billing_repo.update_account(
            conn,
            user_id,
            plan_available=account["plan_available_micro"] + plan_back,
            plan_reserved=account["plan_reserved_micro"] - plan_back,
            prepaid_available=account["prepaid_available_micro"] + prepaid_back,
            prepaid_reserved=account["prepaid_reserved_micro"] - prepaid_back,
            now=now,
        )
        billing_repo.close_reservation(conn, int(reservation["id"]), "released", now)
        released = billing_repo.get_account(conn, user_id)
        assert released is not None
        billing_repo.insert_ledger(
            conn,
            user_id,
            plan_back + prepaid_back,
            released["plan_available_micro"],
            released["prepaid_available_micro"],
            "usage_release",
            reference_id,
            f"预占整笔退回{('：' + reason) if reason else ''}",
            now,
        )


def sweep_stale_reservations(older_than_seconds: int) -> int:
    """把产生于 X 秒前、仍 open 的预占一律退回（服务崩溃/进程死的止血清扫）。"""
    now_dt = datetime.now(UTC)
    cutoff = (now_dt - timedelta(seconds=older_than_seconds)).isoformat()
    with db_connection() as conn:
        stale = billing_repo.list_stale_open_reservations(conn, cutoff)
    for reservation in stale:
        try:
            release(reservation["reference_id"], reason=f"超过 {older_than_seconds}s 未结算")
        except BillingError as exc:
            logger.warning(
                "预占清扫跳过：%s", exc,
                extra={"evt": "billing_sweep_skip", "reference_id": reservation["reference_id"]},
            )
    return len(stale)
