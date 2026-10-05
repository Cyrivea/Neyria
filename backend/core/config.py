import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")

DB_FILE = BACKEND_DIR / "data" / "conversations.db"

# ===== 运行环境 =====
# development / production：控制 API 文档开关等环境差异（main.py 使用）
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# CORS 白名单：逗号分隔的完整 Origin（协议+域名+端口），默认只放行本地开发地址。
# 开发环境前端经 Vite 代理（/api）访问后端，本身是同源请求；白名单管的是不走代理的直连场景。
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    if origin.strip()
]

MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASS = os.getenv("MAIL_PASS", "")
MAIL_HOST = os.getenv("MAIL_HOST", "smtp.qq.com")
# 465 = 隐式 TLS，587 = STARTTLS，auth_service 按端口自动选择握手方式
MAIL_PORT = int(os.getenv("MAIL_PORT", "465"))

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "未配置 SECRET_KEY 环境变量，服务拒绝启动。"
        '请在 .env 中设置一个随机密钥，例如：python -c "import secrets; print(secrets.token_hex(32))"'
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
API_KEY = os.getenv("API_KEY", "").strip()
# 聊天主模型与歌词生产模型统一走环境变量；换 Provider 时只改配置不动代码。
# LLM_PROVIDER 选择 services/llm/factory.py 注册的适配器（目前仅 zhipu，
# 新增 Provider = 实现 ChatClient 协议 + 注册一行，见 C12 / solved.md）。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "zhipu").strip()
CHAT_MODEL = os.getenv("CHAT_MODEL", "glm-4-flash").strip()
LYRICS_MODEL = os.getenv("LYRICS_MODEL", "glm-4-flash").strip()
# 图像走 SiliconFlow Kolors（中文友好；该账号 FLUX 被禁用）
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "Kwai-Kolors/Kolors").strip()
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "Wan-AI/Wan2.2-T2V-A14B").strip()
SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3").strip()
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "https://api.siliconflow.cn/v1/embeddings").strip()
RAG_COLLECTION_NAME = os.getenv("RAG_COLLECTION_NAME", "documents_siliconflow_bge_m3").strip()
RAG_DISTANCE_THRESHOLD = float(os.getenv("RAG_DISTANCE_THRESHOLD", "1.10"))

# 原始上传文档只存服务器受控目录，文件名不直接参与路径拼接；可用环境变量迁移到独立数据盘。
DOCUMENT_STORAGE_DIR = Path(
    os.getenv("DOCUMENT_STORAGE_DIR", str(BACKEND_DIR / "data" / "documents"))
).expanduser()

# ===== 限流配置（core/ratelimit.py 使用，全部可用环境变量覆盖）=====
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

CHAT_RATE_PER_MINUTE = int(os.getenv("CHAT_RATE_PER_MINUTE", "10"))
CHAT_RATE_PER_DAY = int(os.getenv("CHAT_RATE_PER_DAY", "200"))
CAPABILITY_RATE_PER_MINUTE = int(os.getenv("CAPABILITY_RATE_PER_MINUTE", "5"))
LOGIN_RATE_PER_MINUTE = int(os.getenv("LOGIN_RATE_PER_MINUTE", "5"))  # 每 IP+邮箱
REGISTER_RATE_PER_MINUTE = int(os.getenv("REGISTER_RATE_PER_MINUTE", "5"))  # 每 IP
CODE_SEND_COOLDOWN_SECONDS = int(os.getenv("CODE_SEND_COOLDOWN_SECONDS", "60"))
CODE_SEND_PER_EMAIL_PER_DAY = int(os.getenv("CODE_SEND_PER_EMAIL_PER_DAY", "10"))
CODE_SEND_PER_IP_PER_DAY = int(os.getenv("CODE_SEND_PER_IP_PER_DAY", "20"))
UPLOAD_MAX_FILE_MB = int(os.getenv("UPLOAD_MAX_FILE_MB", "30"))
UPLOAD_DAILY_TOTAL_MB = int(os.getenv("UPLOAD_DAILY_TOTAL_MB", "100"))

# 全局请求体上限须大于上传单文件上限（30MB + multipart 编码开销）
BODY_MAX_MB = int(os.getenv("BODY_MAX_MB", "32"))

# 字段级上限（业务层）：1MB 的 body 过得了全局限制，但作为单条消息仍不合理
CHAT_MESSAGE_MAX_CHARS = int(os.getenv("CHAT_MESSAGE_MAX_CHARS", "20000"))
CHAT_HISTORY_MAX_ITEMS = int(os.getenv("CHAT_HISTORY_MAX_ITEMS", "200"))
# G3：发给模型的历史对话 token 预算（不含 system prompt 与当前消息）。
# 保守估算口径见 context_builder.estimate_tokens；超出则从最旧开始丢弃。
CHAT_HISTORY_TOKEN_BUDGET = int(os.getenv("CHAT_HISTORY_TOKEN_BUDGET", "6000"))
CHAT_ATTACHMENTS_MAX_ITEMS = int(os.getenv("CHAT_ATTACHMENTS_MAX_ITEMS", "20"))
PROFILE_MAX_CHARS = int(os.getenv("PROFILE_MAX_CHARS", "5000"))

# 意图分类器：开=规则+便宜模型三级漏斗，off=回滚到 prompt-only 老路
INTENT_CLASSIFIER_ENABLED = os.getenv("INTENT_CLASSIFIER_ENABLED", "on").strip().lower() != "off"

# 追问建议（UI2-③）：回复完成后用同模型补记 2~3 条后续方向。多一次便宜的短调用，
# 失败静默降级为空列表；off 用于压测或成本收紧场景。
FOLLOWUP_SUGGESTIONS_ENABLED = (
    os.getenv("FOLLOWUP_SUGGESTIONS_ENABLED", "on").strip().lower() != "off"
)

# ===== 注入语义层（C25 定标，2026-10-03 实测数据见 solved.md 第 108 条）=====
# bge-m3 上良性短句能冲到 0.681、真注入最低 0.749，旧阈值 0.62 正好压在良性段中间。
# 校准后取 0.72（良性尾部与攻击头部之间 0.068 空隙内，双侧留交量）；
# 短于 8 个字符的文本不烧语义层——那么短要么正则先行命中，要么装不下注入载荷。
INJECTION_SEMANTIC_THRESHOLD = float(os.getenv("INJECTION_SEMANTIC_THRESHOLD", "0.72"))
INJECTION_SEMANTIC_MIN_CHARS = int(os.getenv("INJECTION_SEMANTIC_MIN_CHARS", "8"))

# ===== 计费（商业化阶段 1，《计费与商业化方案.md》）=====
# off=完全不接入 billing；track=记帐（预占+结算流水）但积分不足不拒绝（试验盘数据用）；
# enforce=积分不足在上游调用前拒绝（不按负不留坏账）。默认 off 保证测试/开发环境稳定。
BILLING_MODE = os.getenv("BILLING_MODE", "off").strip().lower()
# 管理员测试发放开关（false 时 /billing/test-grant 403）；发放总额与周期用 env 统一调
BILLING_TEST_GRANTS_ENABLED = os.getenv("BILLING_TEST_GRANTS_ENABLED", "off").strip().lower() == "on"
BILLING_TEST_GRANT_CREDITS = os.getenv("BILLING_TEST_GRANT_CREDITS", "100").strip()
BILLING_TEST_GRANT_PERIOD_DAYS = int(os.getenv("BILLING_TEST_GRANT_PERIOD_DAYS", "30"))
# 各能力调用前的默认预占估值（积分）。多预占部分在结算后退返，保证硬约束“不出现负余额”。
BILLING_LYRICS_RESERVE_CREDITS = os.getenv("BILLING_LYRICS_RESERVE_CREDITS", "3").strip()
BILLING_IMAGE_RESERVE_CREDITS = os.getenv("BILLING_IMAGE_RESERVE_CREDITS", "2").strip()
BILLING_VIDEO_RESERVE_CREDITS = os.getenv("BILLING_VIDEO_RESERVE_CREDITS", "5").strip()
# 上游 Provider 成本估算的环境覆盖（services/provider_cost.py 消费）：JSON 字符串，
# 例{"Wan-AI/Wan2.2-T2V-A14B": {"kind": "per_call", "yuan": "1.50"}}——调价/换源不改代码。
PROVIDER_COST_OVERRIDES_JSON = os.getenv("PROVIDER_COST_OVERRIDES_JSON", "").strip()

# 预占超过这个秒数未被结算/释放视为提交方崩溃，由 sweep 恢复原占用额度。
# 下限约束：必须大于最慢能力的真实耗时。Wan2.2 视频实测单次 3m29s，Provider 排队另算，
# 取 900s 留约 4 倍余量（Spec v1 的 300s 已被差决策记录推翻——会误杀在途视频）。
BILLING_RESERVATION_STALE_SECONDS = int(os.getenv("BILLING_RESERVATION_STALE_SECONDS", "900"))
# Worker 周期性调用 sweep_stale_reservations 的间隔；另外 get_balance 还有请求级的惰性清理双保险。
BILLING_SWEEP_INTERVAL_SECONDS = int(os.getenv("BILLING_SWEEP_INTERVAL_SECONDS", "60"))

# ===== 工具执行超时（秒）：超过即中断并回给模型一条明确的超时结果 =====
TOOL_TIMEOUT_SECONDS = int(os.getenv("TOOL_TIMEOUT_SECONDS", "120"))

# G5-② LLM Provider 显式超时：聊天/意图分类/追问等 SDK 调用不再吃 SDK 默认值，
# 上游挂起最多等这么久即由适配器抛超时（走既有异常→用户可见的转圈永绝后患）。
LLM_REQUEST_TIMEOUT_SECONDS = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "120"))

# ===== E3 持久化任务 Worker =====
JOB_LEASE_SECONDS = int(os.getenv("JOB_LEASE_SECONDS", "60"))
JOB_MAX_ATTEMPTS = int(os.getenv("JOB_MAX_ATTEMPTS", "3"))
JOB_POLL_INTERVAL_SECONDS = float(os.getenv("JOB_POLL_INTERVAL_SECONDS", "1"))
