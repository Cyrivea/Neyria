"""轻量顺序迁移机制（C9）：全部 DDL 的唯一住所。

- 版本号存 `PRAGMA user_version`（写在库文件头部，随事务提交/回滚），
  启动时把 [当前版本+1, 最新] 的迁移按序执行，每个迁移一个事务；
- 改表结构 = 追加一条新迁移，**永远不改历史迁移**（存量库只认序号）；
- SQLite 不支持 ALTER TABLE ADD FOREIGN KEY，补外键只能按官方十二步法重建表
  （见 _m002）：建新表 → 搬数据 → 删旧表 → 改名 → 重建索引。
"""

import logging
import sqlite3
from collections.abc import Callable

from core import database

logger = logging.getLogger("migrations")

Migration = tuple[str, Callable[[sqlite3.Connection], None]]


def _m001_baseline(conn: sqlite3.Connection) -> None:
    """基线：与存量库结构完全一致（users / messages / projects + 索引）。

    IF NOT EXISTS + 补列写法兼容两种起点：全新库从零建齐；
    存量库（user_version=0 但表已在）跑一遍等于 no-op。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            profile TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_id INTEGER,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            attachments_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # 早期版本的 messages 没有这两列，存量库在此补齐
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "project_id" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN project_id INTEGER")
    if "attachments_json" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN attachments_json TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            project_path TEXT NOT NULL,
            save_mode TEXT DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_opened_at TEXT NOT NULL
        )
        """
    )
    # 索引支撑高频查询 WHERE user_id=? [AND project_id=?]（最左前缀覆盖单查 user_id）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_project ON messages(user_id, project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id)")


def _m003_create_jobs(conn: sqlite3.Connection) -> None:
    """创建持久化后台任务表，为 E3 Worker 提供可恢复的任务账本。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            payload_json TEXT NOT NULL,
            result_json TEXT,
            error_message TEXT,
            progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
            progress_message TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
            next_run_at TEXT,
            claimed_by TEXT,
            lease_expires_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, next_run_at, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user_project ON jobs(user_id, project_id, created_at)")


def _m004_create_documents(conn: sqlite3.Connection) -> None:
    """保存原始文档和异步索引生命周期，供 E3 Worker 在重启后恢复。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'cancelled', 'deleted')),
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            chunks_count INTEGER NOT NULL DEFAULT 0 CHECK (chunks_count >= 0),
            error_message TEXT,
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            indexed_at TEXT
        )
        """
    )
    # SQLite 的 NULL 不参与普通 UNIQUE，因此用表达式把“默认知识库”的 NULL 项目归一为 -1。
    conn.execute("DROP INDEX IF EXISTS idx_documents_identity")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_identity "
        "ON documents(user_id, COALESCE(project_id, -1), filename) WHERE status != 'deleted'"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_user_project ON documents(user_id, project_id, updated_at)"
    )


def _m002_add_foreign_keys(conn: sqlite3.Connection) -> None:
    """重建 messages / projects，补上基线欠下的 FOREIGN KEY 声明。

    先清孤儿数据（否则启用外键后 foreign_key_check 报错）：
    归属已不存在用户的行直接删；指向已不存在项目的消息回落到默认会话。
    """
    # 重跑安全：Python sqlite3 的 DDL 在隐式事务外执行，上次中途失败可能残留 _new 表
    conn.execute("DROP TABLE IF EXISTS projects_new")
    conn.execute("DROP TABLE IF EXISTS messages_new")
    conn.execute("DELETE FROM projects WHERE user_id NOT IN (SELECT id FROM users)")
    conn.execute("DELETE FROM messages WHERE user_id NOT IN (SELECT id FROM users)")
    conn.execute(
        "UPDATE messages SET project_id=NULL"
        " WHERE project_id IS NOT NULL AND project_id NOT IN (SELECT id FROM projects)"
    )

    conn.execute(
        """
        CREATE TABLE projects_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            project_path TEXT NOT NULL,
            save_mode TEXT DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_opened_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO projects_new SELECT id, user_id, name, project_path, save_mode,"
        " created_at, updated_at, last_opened_at FROM projects"
    )
    conn.execute("DROP TABLE projects")
    conn.execute("ALTER TABLE projects_new RENAME TO projects")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id)")

    conn.execute(
        """
        CREATE TABLE messages_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            attachments_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO messages_new SELECT id, user_id, project_id, role, content,"
        " attachments_json, created_at FROM messages"
    )
    conn.execute("DROP TABLE messages")
    conn.execute("ALTER TABLE messages_new RENAME TO messages")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_project ON messages(user_id, project_id)")


def _m005_add_document_hash(conn: sqlite3.Connection) -> None:
    """为文档记录内容指纹，便于重复上传识别和后续去重策略。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
    if "file_hash" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN file_hash TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(user_id, file_hash)")


def _m006_add_message_citations(conn: sqlite3.Connection) -> None:
    """保存 RAG 引用元数据，让历史消息刷新后仍能展示来源。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "citations_json" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN citations_json TEXT")


def _m007_create_workflow_drafts(conn: sqlite3.Connection) -> None:
    """保存每个项目的唯一 Workflow Draft；nodes/edges 由后端作为 JSON 校验后持久化。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            project_id_key INTEGER GENERATED ALWAYS AS (COALESCE(project_id, -1)) STORED,
            revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
            draft_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, project_id_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_drafts_user_project "
        "ON workflow_drafts(user_id, project_id)"
    )


def _m008_create_workflow_runs(conn: sqlite3.Connection) -> None:
    """Workflow 异步运行账本：一次运行 + 每个节点一步，长时间生成不再挂在请求里。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            workflow_id INTEGER,
            draft_revision INTEGER NOT NULL,
            draft_snapshot_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            error TEXT,
            job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_steps (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            capability_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            input_json TEXT,
            output_json TEXT,
            error TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_runs_user_project "
        "ON workflow_runs(user_id, project_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflow_steps_run "
        "ON workflow_steps(run_id)"
    )


def _m009_create_usage_events(conn: sqlite3.Connection) -> None:
    """精确用量账本：每次能力调用记录 token、来源和状态，不给用户暴露上游细节。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            capability_id TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('standalone', 'assistant', 'workflow')),
            model TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL CHECK (status IN ('succeeded', 'model_unavailable', 'failed')),
            error TEXT,
            duration_ms REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_events_user_project "
        "ON usage_events(user_id, project_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_events_capability "
        "ON usage_events(capability_id, created_at)"
    )


def _m011_add_run_env_snapshot(conn: sqlite3.Connection) -> None:
    """运行时的“当时环境”快照：prompt/模型/能力清单随 run 落库。

    没有它，发版改了 prompt 或换了模型后，恢复/复查历史运行不能重现当时行为。"""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)")]
    if "env_snapshot_json" not in cols:
        conn.execute("ALTER TABLE workflow_runs ADD COLUMN env_snapshot_json TEXT")


def _m012_create_assets(conn: sqlite3.Connection) -> None:
    """生产资料资产表：每次生成的图片/以后音/视频至少要能回答"

    「它是哪个 user/哪个 project/哪个 run/哪个 prompt 出的、何时、多大、源自哪个报价版本」
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
            run_id TEXT REFERENCES workflow_runs(id) ON DELETE SET NULL,
            capability_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('image','audio','video','text')),
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            prompt_snapshot_json TEXT NULL,
            usage_event_id INTEGER REFERENCES usage_events(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assets_user_project "
        "ON assets(user_id, project_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_assets_run ON assets(run_id)"
    )


def _m013_create_billing(conn: sqlite3.Connection) -> None:
    """商业化阶段 1：订阅 entitlement、价格版本与额度账本地基。

    设计约束（来自《计费与商业化方案.md》§4/§6/§7）：
    - 金额与积分全程定点数：价格以十进制字符串存储（Decimal 无损解析），
      账户/流水以 micro-积分整数存储（1 积分 = 1_000_000 micro），二进制浮点不进库；
    - 历史账单必须引用调用发生时的价格版本：usage_events 三列在此补齐；
    - credit_ledger 不可变：UPDATE/DELETE 触发器直接拒绝，余额只能从流水重建/解释；
    - 原子预占需要的账户行带 CHECK 非负约束，配合 BEGIN IMMEDIATE 串行写保证不透支。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_price_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL,
            -- 积分/千 token，十进制字符串（Python Decimal 可无损解析）；不用 REAL
            input_price_per_1k TEXT NOT NULL,
            output_price_per_1k TEXT NOT NULL,
            cache_price_per_1k TEXT NOT NULL DEFAULT '0',
            reasoning_price_per_1k TEXT NOT NULL DEFAULT '0',
            -- 无 token 概念的能力（图像/音频/视频）固定每次调用价，积分/次
            per_call_credits TEXT NOT NULL DEFAULT '0',
            credit_multiplier TEXT NOT NULL DEFAULT '1',
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_versions_model "
        "ON model_price_versions(model_id, effective_from)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'expired', 'cancelled', 'past_due')),
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            included_credits_micro INTEGER NOT NULL CHECK (included_credits_micro >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subscriptions_user "
        "ON subscriptions(user_id, status, period_end)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS credit_accounts (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            -- 两个桶分源记录（套餐额度 / 预付余额），各自分可用与已预占
            plan_available_micro INTEGER NOT NULL DEFAULT 0 CHECK (plan_available_micro >= 0),
            plan_reserved_micro INTEGER NOT NULL DEFAULT 0 CHECK (plan_reserved_micro >= 0),
            prepaid_available_micro INTEGER NOT NULL DEFAULT 0 CHECK (prepaid_available_micro >= 0),
            prepaid_reserved_micro INTEGER NOT NULL DEFAULT 0 CHECK (prepaid_reserved_micro >= 0),
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS credit_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reference_id TEXT NOT NULL UNIQUE,  -- 幂等键：调用方生成
            plan_micro INTEGER NOT NULL CHECK (plan_micro >= 0),
            prepaid_micro INTEGER NOT NULL CHECK (prepaid_micro >= 0),
            status TEXT NOT NULL CHECK (status IN ('open', 'settled', 'released')),
            created_at TEXT NOT NULL,
            closed_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_credit_reservations_user "
        "ON credit_reservations(user_id, status)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS credit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            amount_micro INTEGER NOT NULL,  -- 正=进账/退回，负=扣减/预占
            plan_balance_after_micro INTEGER NOT NULL,
            prepaid_balance_after_micro INTEGER NOT NULL,
            type TEXT NOT NULL CHECK (type IN (
                'subscription_grant', 'usage_reserve', 'usage_settlement', 'usage_release',
                'top_up', 'refund', 'expiration', 'manual_adjustment'
            )),
            reference_id TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_ledger_type_ref "
        "ON credit_ledger(type, reference_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_credit_ledger_user "
        "ON credit_ledger(user_id, created_at)"
    )
    # 流水只能追加，不能改写/删除——余额变化永远可解释
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON credit_ledger
        BEGIN SELECT RAISE(ABORT, 'credit_ledger 是不可变流水，禁止 UPDATE'); END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON credit_ledger
        BEGIN SELECT RAISE(ABORT, 'credit_ledger 是不可变流水，禁止 DELETE'); END
        """
    )
    # usage_events 补价格快照与记费结果列（历史账引用调用时的价格版本）
    usage_columns = {row[1] for row in conn.execute("PRAGMA table_info(usage_events)").fetchall()}
    if "price_version_id" not in usage_columns:
        conn.execute(
            "ALTER TABLE usage_events ADD COLUMN price_version_id INTEGER "
            "REFERENCES model_price_versions(id) ON DELETE SET NULL"
        )
    if "billed_credits_micro" not in usage_columns:
        conn.execute("ALTER TABLE usage_events ADD COLUMN billed_credits_micro INTEGER")
    if "provider_cost_micro" not in usage_columns:
        conn.execute("ALTER TABLE usage_events ADD COLUMN provider_cost_micro INTEGER")


def _m010_add_message_interrupted(conn: sqlite3.Connection) -> None:
    """SSE 断连截断标记：消息可能只生成了一半（客户端断开/上游中断），
    落库时必须带标，否则用户重进看到的是一条莫名的话。"""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
    if "interrupted" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0")


def _m014_project_panel_semantics(conn: sqlite3.Connection) -> None:
    """B29「最近打开」面板语义：登记 ≠ 保存。

    - saved_at：首次真实保存（自动保存写盘/用户显式保存）的时刻，面板只显示已保存项目；
    - discarded_at：用户「不保存」放弃未保存过的工程的时刻，面板隐藏但行不删（外键链不断）；
    - 存量回填：旧项目按旧语义一律视作已保存，面板行为不回退。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
    if "saved_at" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN saved_at TEXT")
    if "discarded_at" not in columns:
        conn.execute("ALTER TABLE projects ADD COLUMN discarded_at TEXT")
    conn.execute("UPDATE projects SET saved_at = COALESCE(saved_at, updated_at)")


def _m015_add_message_feedback(conn: sqlite3.Connection) -> None:
    """UI2-⑤：助手消息的用户有力度评分（1=👍 / -1=👎 / NULL=未评）。

    和 usage_events 账本互补：usage 记「花了多少」，feedback 记「好不好」，
    后续给 W2/C16 意图评测当标注语料时一条 SELECT 就够。"""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
    if "feedback" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN feedback INTEGER")


def _m016_workflow_run_keep_flag(conn: sqlite3.Connection) -> None:
    """WF1-③ 队列档案馆：「满意才保留」旗标。

    运行即成账不退款，删除=清记录+产物文件；keep=1 的运行是未来自动清理策略的
    豁免名单，也是用户在档案馆里标记「这版要留」的入口。"""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)")]
    if "kept" not in cols:
        conn.execute("ALTER TABLE workflow_runs ADD COLUMN kept INTEGER NOT NULL DEFAULT 0")


MIGRATIONS: list[Migration] = [
    ("baseline: users/messages/projects + indexes", _m001_baseline),
    ("add foreign keys via table rebuild", _m002_add_foreign_keys),
    ("create persistent jobs table", _m003_create_jobs),
    ("create document lifecycle table", _m004_create_documents),
    ("add document content hash", _m005_add_document_hash),
    ("add message citation metadata", _m006_add_message_citations),
    ("create workflow drafts", _m007_create_workflow_drafts),
    ("create workflow run/step ledger", _m008_create_workflow_runs),
    ("create usage ledger", _m009_create_usage_events),
    ("add message interrupted flag", _m010_add_message_interrupted),
    ("add run env snapshot", _m011_add_run_env_snapshot),
    ("create assets ledger", _m012_create_assets),
    ("create billing core (prices/subscriptions/credits)", _m013_create_billing),
    ("project panel semantics: saved_at + discarded_at", _m014_project_panel_semantics),
    ("add message user feedback", _m015_add_message_feedback),
    ("workflow run keep flag (queue archive)", _m016_workflow_run_keep_flag),
]


def run_migrations() -> None:
    """把数据库推进到最新版本。幂等：已是最新则什么都不做。"""
    conn = database.get_connection()
    try:
        # 重建表期间必须关外键（否则 DROP TABLE 被引用检查拦住）；
        # PRAGMA foreign_keys 在事务内是 no-op，所以要在开事务前执行
        conn.execute("PRAGMA foreign_keys=OFF")

        current = conn.execute("PRAGMA user_version").fetchone()[0]
        latest = len(MIGRATIONS)
        if current > latest:
            raise RuntimeError(
                f"数据库版本 {current} 超过代码已知最新版本 {latest}，可能在跑旧代码，拒绝启动以免损坏数据"
            )
        for number in range(current + 1, latest + 1):
            name, apply = MIGRATIONS[number - 1]
            with conn:  # 一个迁移一个事务：中途失败整体回滚，user_version 不前进
                apply(conn)
                conn.execute(f"PRAGMA user_version={number}")
            logger.info("迁移完成", extra={"evt": "migration_applied", "version": number, "migration": name})
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"迁移后外键校验失败：{violations[:5]}")
    finally:
        conn.close()
