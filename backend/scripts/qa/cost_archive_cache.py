"""报价/缓存/档案馆全链真机验收：歌词链（真调 LLM）→ 复用 0 积分 → 参数变化转 stale。

用法：cd backend && uv run python scripts/qa/cost_archive_cache.py
这是「跑一次脚本＝对财务可信链路体检」的入口。
"""
import json
import random
import string
import subprocess
import sys
import time
import urllib.request

ROOT = "/home/cyrivea/Neyria/backend"
BASE = "http://127.0.0.1:18199"

api = subprocess.Popen(["./.venv/bin/python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "18199"],
    cwd=ROOT, stdout=open("/tmp/qa-c-api.log", "w"), stderr=subprocess.STDOUT)
wk = subprocess.Popen(["./.venv/bin/python", "-m", "workers.job_worker"],
    cwd=ROOT, stdout=open("/tmp/qa-c-wk.log", "w"), stderr=subprocess.STDOUT)

FAILED = []
def expect(c, label):
    print(("PASS " if c else "FAIL ") + label)
    if not c: FAILED.append(label)

def req(m, p, b=None, timeout=240):
    r = urllib.request.Request(BASE + p, method=m,
        data=json.dumps(b).encode() if b is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")

TOKEN = ""
try:
    for _ in range(90):
        try: urllib.request.urlopen(BASE + "/health", timeout=2); break
        except Exception: time.sleep(0.5)
    import redis as _redis
    email = "qa-cost-" + "".join(random.choices(string.ascii_lowercase, k=6)) + "@example.com"
    r = _redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    r.hset(f"authcode:{email}", mapping={"code": "123456", "attempts": 0}); r.expire(f"authcode:{email}", 600)
    TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + "/auth/register", method="POST",
        data=json.dumps({"username": email, "password": "Test123456", "code": "123456"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read())["token"]
    print("QA account:", email)

    # S0 搭一条纯歌词链
    d = req("GET", "/workflows/draft")
    d["draft"]["nodes"] = [
        {"id": "workflow-input", "kind": "endpoint", "endpoint": "input", "name": "输入", "x": 36, "y": 280},
        {"id": "n-lyr", "kind": "tool", "type": "lyrics", "name": "写歌词", "capability_id": "lyrics.generate",
         "params": {"theme": "雨夜归途"}, "x": 300, "y": 280},
        {"id": "workflow-output", "kind": "endpoint", "endpoint": "output", "name": "输出", "x": 600, "y": 280},
    ]
    d["draft"]["edges"] = [
        {"source": "workflow-input", "target": "n-lyr"},
        {"source": "n-lyr", "target": "workflow-output"},
    ]
    saved = req("PUT", "/workflows/draft", {"project_id": None, "expected_revision": d["revision"], "draft": d["draft"]})
    expect("revision" in saved, "S0 草稿落库")

    # S1 冷启动预览：3 积分（歌词预占）
    p1 = req("GET", "/workflows/run-preview")
    assert p1["ready"], "冷启动预览必须 ready"
    expect(p1["total_cost_credits"] == 3.0, f"S1 冷启动预占价（实际 {p1['total_cost_credits']}）")
    expect(p1["chains"][0]["blocks"][0]["state"] == "cold", "S1 状态是 cold")

    # S2 真跑
    sub = req("POST", "/workflows/run")
    job_id = sub["job_id"]
    t_end = time.time() + 420
    job = {}
    while time.time() < t_end:
        job = req("GET", f"/jobs/{job_id}")
        if job["status"] in ("succeeded", "failed", "cancelled"): break
        time.sleep(3)
    expect(job.get("result", {}).get("status") == "succeeded",
           f"S2 跑通（job.status={job.get('status')} err={job.get('result', {}).get('error')}）")

    # S3 复用预览：歌词命中缓存 → 0 积分
    p2 = req("GET", "/workflows/run-preview")
    expect(p2["total_cost_credits"] == 0.0, f"S3 复用免费（实际 {p2['total_cost_credits']}）")
    expect(p2["chains"][0]["blocks"][0]["state"] == "reused", "S3 状态是 reused")

    # S4 改主题 → stale 计费 + 命中数回落
    d2 = req("GET", "/workflows/draft")
    for n in d2["draft"]["nodes"]:
        if n.get("id") == "n-lyr": n["params"]["theme"] = "深夜食堂"
    req("PUT", "/workflows/draft", {"project_id": None, "expected_revision": d2["revision"], "draft": d2["draft"]})
    p3 = req("GET", "/workflows/run-preview")
    expect(p3["total_cost_credits"] == 3.0, f"S4 改主题重计费（实际 {p3['total_cost_credits']}）")
    expect(p3["chains"][0]["blocks"][0]["state"] == "stale", "S4 状态是 stale")

    # S5 档案馆 + keep 往返 + 删除不退款
    lst = req("GET", "/workflows/runs")
    run = next(r for r in lst["runs"] if r["run_id"] == sub["run_id"])
    expect(run["kept"] is False, "S5 默认不保留")
    req("PATCH", f"/workflows/runs/{sub['run_id']}/keep", {"kept": True})
    reversed_run = req("GET", "/workflows/runs")
    expect(next(r for r in reversed_run["runs"] if r["run_id"] == sub["run_id"])["kept"] is True, "S5 keep 置位")
    deleted = req("DELETE", f"/workflows/runs/{sub['run_id']}")
    expect(deleted["deleted"] is True and deleted["refund"] is False, "S6 删除不退款（诚实）")
    lst = req("GET", "/workflows/runs")
    expect(not any(r["run_id"] == sub["run_id"] for r in lst["runs"]), "S6 删除后列表干净")
finally:
    api.terminate(); wk.terminate()
    print(f"\n=== 账本/缓存验收收尾：{len(FAILED)} FAIL ===")
    sys.exit(1 if FAILED else 0)
