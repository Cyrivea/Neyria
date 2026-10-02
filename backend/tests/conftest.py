"""tests 全局夹具：在任何被测模块 import 之前准备测试环境变量。

conftest 由 pytest 在收集阶段最先加载，早于测试模块的顶层 import，
所以这里 setdefault 的值能被 core/config.py 读到。
"""

import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key")


@pytest.fixture(scope="session", autouse=True)
def _isolate_db(tmp_path_factory):
    """C22：把整个测试会话的 SQLite 重定向到临时目录并跑迁移，
    本地 pytest 绝不再写 data/conversations.db（usage_events 曾被污染 110 条造假失败记录，
    见 solved.md C22 条目）。需要独立小库做精确断言的测试照旧自己
    monkeypatch database.DB_FILE——测试体里的 monkeypatch 在本 fixture setup 之后执行，照常生效。"""
    import core.database as database

    database.DB_FILE = tmp_path_factory.mktemp("pytest-db") / "conversations.db"
    from core.migrations import run_migrations

    run_migrations()


@pytest.fixture(autouse=True)
def _billing_mode_off(monkeypatch):
    """测试默认关闭计费门控：老的用例不应被“积分不足”拒绝；
    计费测试自己显式 monkeypatch 成 track/enforce。"""
    monkeypatch.setattr("core.config.BILLING_MODE", "off")


@pytest.fixture(autouse=True)
def _followups_off(monkeypatch):
    """测试默认关闭追问建议（UI2-③）：它每轮多调一次模型，会打破存量用例的
    「模型调用次数」断言。需要覆盖该功能的测试自己 monkeypatch 打开。"""
    monkeypatch.setattr("core.config.FOLLOWUP_SUGGESTIONS_ENABLED", False)
