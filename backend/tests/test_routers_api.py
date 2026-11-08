"""routers 层 API 接线测试（F10）：挂真实 main.app 的 TestClient。

与 service 层测试的分工：这里只验「路由接线」——鉴权依赖、中间件、状态码、
响应形状。业务正确性已由 service 层 400+ 用例锁定，这里不重复造轮子；
真实 Provider（LLM/生图/生视频）一律不触碰——涉能力的用例只测
「无密钥拒绝」与「未知能力拒绝」这类不需要出网的边界。

历史教训（为什么这个文件值得存在）：存量 397 用例全在 service 层，唯一
FastAPI 测试（test_body_limits）跑自建迷你 app——routers/*.py、assistant_service、
schemas/billing、core/request_context 的覆盖率全部为 0%（F10）。CI 里
regenerate/edit 互斥校验顺序错误（4eeddb1）就是这样漏掉进主干红的。
"""


import pytest
from fastapi.testclient import TestClient

from core.database import db_connection
from services.auth_service import hash_password

USERNAME = "api-e2e@local.test"
PASSWORD = "Api#2026"


@pytest.fixture(autouse=True)
def _relax_login_rate(monkeypatch):
    """本模块大量即 429 的登录用例：把限流撞高到测试射程之外。

    限流本身的行为已由 test_ratelimit 锁定；这里只是避免「计数跨 pytest 运行保留在
    Redis 里」把接线测试误杀（真机实证：第二轮运行被 429 拦了 36 秒）。
    """
    monkeypatch.setattr("core.config.LOGIN_RATE_PER_MINUTE", 1000)
    monkeypatch.setattr("core.config.CAPABILITY_RATE_PER_MINUTE", 1000)
    monkeypatch.setattr("core.config.CHAT_RATE_PER_MINUTE", 1000)


@pytest.fixture(scope="module")
def client():
    from main import app

    # with 语法触发 startup（迁移幂等 + Redis ping）——生产启动路径一并覆盖
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def auth_headers(client):
    """直插用户（register 走邮箱验证码，不属接线 happy path），再走 /auth/login 拿 token。"""
    with db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, profile, created_at) "
            "VALUES (?, ?, ?, ?)",
            (USERNAME, hash_password(PASSWORD), "", "2026-01-01"),
        )
    resp = client.post("/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    assert token
    return {"Authorization": f"Bearer {token}"}


# ---------- 基础接线：health / request_id ----------

def test_health_open_without_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_id_header_on_every_response(client, auth_headers):
    """RequestContextMiddleware：任何响应都带 X-Request-ID（前端报错时可展示）。"""
    resp = client.get("/projects/recent", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")


# ---------- auth ----------

def test_login_wrong_password_401(client):
    resp = client.post("/auth/login", json={"username": USERNAME, "password": "wrong-password"})
    assert resp.status_code == 401


def test_login_unknown_user_same_error_text(client):
    """A9 登录时序侧信道：未知用户与错误密码必须同一句报错（不给「用户不存在」的信号）。"""
    r_unknown = client.post("/auth/login", json={"username": "ghost@local.test", "password": "x" * 10})
    r_wrong = client.post("/auth/login", json={"username": USERNAME, "password": "wrong-password"})
    assert r_unknown.status_code == r_wrong.status_code == 401
    assert r_unknown.json()["detail"] == r_wrong.json()["detail"]


def test_login_missing_fields_422(client):
    resp = client.post("/auth/login", json={"username": USERNAME})
    assert resp.status_code == 422


# ---------- 鉴权边界：无 token 一律拒 ----------

@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/projects/recent"),
        ("GET", "/billing/balance"),
        ("GET", "/jobs"),
        ("GET", "/workflows/draft"),
        ("GET", "/workflows/runs"),
        ("POST", "/chat"),
    ],
)
def test_protected_routes_reject_missing_token(client, method, path):
    resp = client.request(method, path, json={} if method == "POST" else None)
    assert resp.status_code in (401, 403), f"{method} {path} 应被鉴权拦截，实际 {resp.status_code}"


def test_invalid_token_rejected(client):
    resp = client.get("/projects/recent", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code in (401, 403)


# ---------- projects ----------

def test_project_create_get_discard_roundtrip(client, auth_headers):
    created = client.post("/projects", headers=auth_headers, json={"name": "API 测试工程", "project_path": "api-test"})
    assert created.status_code == 200
    project = created.json()["project"]
    assert project["id"] > 0

    recent = client.get("/projects/recent", headers=auth_headers)
    ids = [item["id"] for item in recent.json()["items"]]
    # B29 语义：最近列表只显示「已保存且未放弃」——新建后先标保存
    if project["id"] not in ids:
        saved_flag = client.post(f"/projects/{project['id']}/saved", headers=auth_headers)
        assert saved_flag.status_code == 200
        recent = client.get("/projects/recent", headers=auth_headers)
        ids = [item["id"] for item in recent.json()["items"]]
    assert project["id"] in ids

    got = client.get(f"/projects/{project['id']}", headers=auth_headers)
    assert got.status_code == 200

    discarded = client.post(f"/projects/{project['id']}/discard", headers=auth_headers)
    assert discarded.status_code == 200
    # 放弃后从最近列表消失（B29 语义）
    recent_after = client.get("/projects/recent", headers=auth_headers)
    assert project["id"] not in [item["id"] for item in recent_after.json()["items"]]


def test_project_get_others_404(client, auth_headers):
    """越权访问：不存在的项目 id 与「不是你的项目」必须同口径 404。"""
    resp = client.get("/projects/999999", headers=auth_headers)
    assert resp.status_code == 404


# ---------- billing ----------

def test_billing_balance_shape_for_fresh_user(client, auth_headers):
    resp = client.get("/billing/balance", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # 新用户零余额：四元组字符串出网（Decimal 不进 JSON）
    assert data["plan_credits"] == "0"
    assert data["prepaid_credits"] == "0"
    assert data["plan_reserved_credits"] == "0"
    assert data["prepaid_reserved_credits"] == "0"
    assert isinstance(data["recent_ledger"], list)


def test_billing_test_grant_gated_off_by_default(client, auth_headers):
    """环境门控：默认 off 时模拟放款接口必须 403 拒绝（生产防误开）。"""
    resp = client.post("/billing/test-grant", headers=auth_headers, json={})
    assert resp.status_code == 403
    assert "未启用" in resp.json()["detail"]


def test_billing_usage_summary_days_clamped(client, auth_headers):
    resp = client.get("/billing/usage-summary?days=99999", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["days"] == 365  # 上限钳制


# ---------- jobs ----------

def test_jobs_list_empty_and_unknown_404(client, auth_headers):
    assert client.get("/jobs", headers=auth_headers).json() == []
    resp = client.get("/jobs/job-does-not-exist", headers=auth_headers)
    assert resp.status_code == 404


# ---------- workflows ----------

def test_workflow_draft_roundtrip_and_revision(client, auth_headers):
    first = client.get("/workflows/draft", headers=auth_headers)
    assert first.status_code == 200
    base_revision = first.json()["revision"]

    draft_payload = {"nodes": [{"id": "n1", "kind": "tool", "capability_id": "lyrics.generate"}], "edges": [], "chains": []}
    saved = client.put(
        "/workflows/draft",
        headers=auth_headers,
        json={"draft": draft_payload, "expected_revision": base_revision, "project_id": None},
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] >= 1

    # 过期 revision 保存必须被 CAS 拒绝（B19 并发编辑防线）
    stale = client.put(
        "/workflows/draft",
        headers=auth_headers,
        json={"draft": draft_payload, "expected_revision": 0, "project_id": None},
    )
    assert stale.status_code == 409


def test_workflow_runs_latest_404_when_never_run(client, auth_headers):
    resp = client.get("/workflows/runs/latest", headers=auth_headers)
    assert resp.status_code == 404


def test_workflow_run_preview_ready_false_for_empty_canvas(client, auth_headers):
    """空画布的跑前报价：不 ready 且不给费用（与 _execution_order 拒绝语义一致）。"""
    resp = client.get("/workflows/run-preview", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False


# ---------- chat 接线 ----------

def test_chat_regenerate_edit_mutual_exclusion_400(client, auth_headers, monkeypatch):
    """4eeddb1 回归锁（HTTP 层）：互斥参数必须在响应头发出前被拒成 400。

    A1 接线测试抓到的真 bug：校验原在 SSE 生成器里，响应头已发出后 raise 只能
    变 500 断流；修复后路由层先行校验（服务层检查保留作双保险）。
    """
    monkeypatch.setattr("services.chat.chat_orchestrator.client", None)  # 双保险：绝不碰真网
    resp = client.post(
        "/chat",
        headers=auth_headers,
        json={"message": "测试", "regenerate": True, "edit_from_id": 1},
    )
    assert resp.status_code == 400
    assert "互斥" in resp.json()["detail"]


def test_chat_overlong_message_422(client, auth_headers):
    """超长消息被 schema 拒在路由外（CHAT_MESSAGE_MAX_CHARS）。"""
    from core import config

    resp = client.post(
        "/chat",
        headers=auth_headers,
        json={"message": "长" * (config.CHAT_MESSAGE_MAX_CHARS + 1)},
    )
    assert resp.status_code == 422


def test_history_empty_for_fresh_user(client, auth_headers):
    resp = client.get("/history", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


# ---------- capabilities 接线（不出网）----------

def test_capability_run_unknown_rejected_at_schema(client, auth_headers):
    """能力白名单是 Literal 枚举：未知 capability_id 在 schema 层就是 422，不进执行器。"""
    resp = client.post(
        "/capabilities/run",
        headers=auth_headers,
        json={"capability_id": "nonsense.generate", "inputs": {"prompt": "x"}},
    )
    assert resp.status_code == 422


def test_capability_run_video_without_key_model_unavailable(client, auth_headers, monkeypatch):
    """无密钥的 Provider 必须在碰网络前返回 model_unavailable（离线可跑、绝不出网）。"""
    monkeypatch.setattr("core.config.VIDEO_PROVIDER", "siliconflow")
    monkeypatch.setattr("core.config.SILICONFLOW_API_KEY", "")
    resp = client.post(
        "/capabilities/run",
        headers=auth_headers,
        json={
            "capability_id": "video.generate",
            "inputs": {"prompt": "夜晚湖面", "style": "电影写实", "ratio": "16:9", "duration": "5 秒"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "model_unavailable"


def test_capability_project_ownership_404(client, auth_headers):
    """挂不存在的 project_id 直接 404，不进执行器。"""
    resp = client.post(
        "/capabilities/run",
        headers=auth_headers,
        json={"capability_id": "lyrics.generate", "project_id": 999999, "inputs": {"theme": "x"}},
    )
    assert resp.status_code == 404


# ---------- assets ----------

def test_assets_require_auth(client):
    resp = client.get("/api/assets/nonexistent.png")
    assert resp.status_code in (401, 403, 404)
