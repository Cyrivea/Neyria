"""I3 上游成本估算：估算口径的可信度靠三条——已核定免费=0、定价页实价、未收录不造假。"""

from __future__ import annotations

from core import database, migrations
from services import provider_cost


def test_known_free_models_cost_zero():
    assert provider_cost.estimate_cost_micro("glm-4-flash", 10_000, 10_000) == 0
    assert provider_cost.estimate_cost_micro("BAAI/bge-m3", 5_000) == 0
    assert provider_cost.estimate_cost_micro("Kwai-Kolors/Kolors") == 0


def test_video_model_per_call_priced():
    # ¥2.00/枚 → 2_000_000 micro 元（2026-10-03 供应商定价页权威值）
    assert provider_cost.estimate_cost_micro("Wan-AI/Wan2.2-T2V-A14B") == 2_000_000


def test_unknown_model_returns_none_not_zero():
    """未收录模型返回 None：NULL=未校准是诚实状态，写 0 会把「未知」伪装成「免费」。"""
    assert provider_cost.estimate_cost_micro("openai/gpt-99-ultra") is None
    assert provider_cost.estimate_cost_micro(None) is None


def test_env_override_recalibrates_without_code_change(monkeypatch):
    """调价只改环境变量，不改代码——SPEC/账单调整零部署成本。"""
    monkeypatch.setattr(
        "core.config.PROVIDER_COST_OVERRIDES_JSON",
        '{"Wan-AI/Wan2.2-T2V-A14B": {"kind": "per_call", "yuan": "1.50"},'
        ' "zhipu/glm-4.6": {"kind": "token", "input_yuan_per_1k": "0.004", "output_yuan_per_1k": "0.016"}}',
    )
    assert provider_cost.estimate_cost_micro("Wan-AI/Wan2.2-T2V-A14B") == 1_500_000
    # 1000 in + 1000 out = 0.004 + 0.016 = ¥0.02 = 20_000 micro
    assert provider_cost.estimate_cost_micro("zhipu/glm-4.6", 1000, 1000) == 20_000


def test_runtime_attaches_provider_cost_to_usage_event(tmp_path, monkeypatch):
    """真机链路：capability 成功后，usage_events 行必须带上 provider_cost_micro。"""
    import asyncio

    from repositories import usage_repo
    from services.capabilities import runtime

    monkeypatch.setattr(database, "DB_FILE", tmp_path / "pc.db")
    migrations.run_migrations()
    with database.db_connection() as conn:
        conn.execute("INSERT INTO users (username, password_hash) VALUES ('pc@x.com','h')")
        uid = conn.execute("SELECT id FROM users").fetchone()[0]

    from dataclasses import replace

    stub = replace(
        runtime.CAPABILITY_REGISTRY["image.generate"],
        executor=lambda inputs: {
            "content": "ok",
            "usage": {"model": "Wan-AI/Wan2.2-T2V-A14B", "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
    )
    monkeypatch.setitem(runtime.CAPABILITY_REGISTRY, "image.generate", stub)

    result = asyncio.run(
        runtime.run_capability(
            "image.generate",
            {"prompt": "x"},
            context=runtime.CapabilityContext(user_id=uid, project_id=None, source="standalone"),
        )
    )
    assert result.status == "succeeded"

    events = usage_repo.list_for_user(uid)
    assert len(events) == 1
    # provider_cost_micro 不在 list_for_user 的常用读列里，直查这一列
    with database.db_connection() as conn:
        row = conn.execute(
            "SELECT provider_cost_micro FROM usage_events WHERE user_id=?", (uid,)
        ).fetchone()
    assert row[0] == 2_000_000
