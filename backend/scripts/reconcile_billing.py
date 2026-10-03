"""账务对账脚本：《Spec v1》§2.5「对账任务每日重算」的一次性先行版。

三条不变量（任一被破坏 = 账本与事实脱节，退出码 1）：

1. 流水重算 = 可用余额：对每个用户，`SUM(credit_ledger.amount_micro)` 必须等于
   账户两列 available 之和。因为 reserve 记负（可用被移出）、release 记正（退回可用），
   净和恰好是「真实可动用权益」；冻结部分由不变量 3 单独核对，不可混入本式；
2. 账户可用余额 = 最新流水的快照：ledger 每行都带事后快照，取最新一行与
   `credit_accounts` 当前值对照；
3. 冻结合约账实相符：open 状态的 credit_reservations 按桶求和，必须等于
   账户的 *_reserved_micro 两列。

用法：
    uv run python scripts/reconcile_billing.py            # 全绿静默退出 0
    有差异时逐用户打印明细并以 1 退出（适合接 CI / cron 告警）。

注意：请在无在途调用时对账（或容忍正在执行中的那几秒偏差人工复核），
流式生成进行中 reserve 已扣而 settle 未到时，不变量 1 的等式仍成立，
但不变量 2 的快照在新流水写入瞬间天然滞后一拍。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

# 与仓库其他 scripts/ 同一约定：独立运行时可从 backend/ 任何方式启动
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.database import db_connection

EPSILON = 0  # micro 精度，零容差


def main() -> int:
    failures: list[str] = []
    with db_connection() as conn:
        conn.row_factory = __import__("sqlite3").Row

        ledger_sums: dict[int, int] = defaultdict(int)
        latest_snapshot: dict[int, tuple[int, int]] = {}
        for row in conn.execute(
            "SELECT id, user_id, amount_micro, plan_balance_after_micro, prepaid_balance_after_micro "
            "FROM credit_ledger ORDER BY id"
        ):
            uid = int(row["user_id"])
            ledger_sums[uid] += int(row["amount_micro"])
            latest_snapshot[uid] = (
                int(row["plan_balance_after_micro"]),
                int(row["prepaid_balance_after_micro"]),
            )

        open_reserved: dict[int, tuple[int, int]] = defaultdict(lambda: (0, 0))
        for row in conn.execute(
            "SELECT user_id, plan_micro, prepaid_micro FROM credit_reservations WHERE status='open'"
        ):
            uid = int(row["user_id"])
            prev = open_reserved[uid]
            open_reserved[uid] = (prev[0] + int(row["plan_micro"]), prev[1] + int(row["prepaid_micro"]))

        accounts = {
            int(r["user_id"]): r
            for r in conn.execute("SELECT * FROM credit_accounts")
        }

    user_ids = set(accounts) | set(ledger_sums)
    for uid in sorted(user_ids):
        acc = accounts.get(uid)
        if acc is None:
            failures.append(f"user {uid}: 有流水但无账户行")
            continue
        plan_avail = int(acc["plan_available_micro"])
        plan_res = int(acc["plan_reserved_micro"])
        prepaid_avail = int(acc["prepaid_available_micro"])
        prepaid_res = int(acc["prepaid_reserved_micro"])

        # 不变量 1：流水净和 = 可用两列（冻结由不变量 3 单独对齐，不能重叡计入）
        if abs(ledger_sums[uid] - (plan_avail + prepaid_avail)) > EPSILON:
            failures.append(
                f"user {uid}: 流水净和 {ledger_sums[uid]} micro != 可用余额 "
                f"{plan_avail + prepaid_avail} micro"
            )

        # 不变量 2：最新快照 = 当前可用
        if uid in latest_snapshot:
            snap_plan, snap_prepaid = latest_snapshot[uid]
            if snap_plan != plan_avail or snap_prepaid != prepaid_avail:
                failures.append(
                    f"user {uid}: 最新流水快照 (plan={snap_plan}, prepaid={snap_prepaid}) "
                    f"!= 账户可用 (plan={plan_avail}, prepaid={prepaid_avail})"
                )

        # 不变量 3：open 预占之和 = 冻结两列
        res_plan, res_prepaid = open_reserved.get(uid, (0, 0))
        if res_plan != plan_res or res_prepaid != prepaid_res:
            failures.append(
                f"user {uid}: open 预占合计 (plan={res_plan}, prepaid={res_prepaid}) "
                f"!= 账户冻结列 (plan={plan_res}, prepaid={prepaid_res})"
            )

    with db_connection() as conn:
        ledger_count = int(conn.execute("SELECT COUNT(*) FROM credit_ledger").fetchone()[0])

    if failures:
        print(f"[reconcile] 发现 {len(failures)} 处账务差异：")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"[reconcile] OK：{len(accounts)} 个账户、{ledger_count} 条流水，三条不变量全部成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
