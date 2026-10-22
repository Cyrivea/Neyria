"""MV 生产线全链真机验收：歌词 → 依词封面 → MV。

用法（后端目录下，uv 已锁依赖）：
    uv run python scripts/qa/full_pipeline.py
依赖：本地 Redis（用 dev 库就行）、.env 里的 KUNBOT_API_KEY、上游余额/路由权限。
自剑 API+Worker 自带生命周期，退散即清。

教训史：2026-10-19 运维人在此过一次在当前平台层都没做好贵手入的「冷门坑」
（拉不出来上游账号责任也顺手归你）。
"""
import json
import random
import string
import subprocess
import sys
import time
import urllib.request

ROOT = "/home/cyrivea/Neyria/backend"
BASE = "http://127.0.0.1:18777"

api = subprocess.Popen(
    ["./.venv/bin/python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "18777"],
    cwd=ROOT, stdout=open("/tmp/qa-mv-api.log", "w"), stderr=subprocess.STDOUT,
)
wk = subprocess.Popen(
    ["./.venv/bin/python", "-m", "workers.job_worker"],
    cwd=ROOT, stdout=open("/tmp/qa-mv-wk.log", "w"), stderr=subprocess.STDOUT,
)

FAILED: list[str] = []
def expect(cond, label):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond: FAILED.append(label)

def req(m, p, b=None, timeout=240):
    r = urllib.request.Request(BASE + p, method=m,
        data=json.dumps(b).encode() if b is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")

def sse_chat(msg, timeout=300):
    r = urllib.request.Request(BASE + "/chat", method="POST",
        data=json.dumps({"message": msg, "project_id": None}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    raw = urllib.request.urlopen(r, timeout=timeout).read().decode("utf-8", "replace")
    evs = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                try: evs.append(json.loads(line[6:]))
                except Exception: pass
    return evs

TOKEN = ""
try:
    for _ in range(90):
        try: urllib.request.urlopen(BASE + "/health", timeout=2); break
        except Exception: time.sleep(0.5)
    # —— 全新 QA 账号（零历史污染）——
    import redis as _redis
    email = "qa-mv-" + "".join(random.choices(string.ascii_lowercase, k=6)) + "@example.com"
    r = _redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    r.hset(f"authcode:{email}", mapping={"code": "123456", "attempts": 0}); r.expire(f"authcode:{email}", 600)
    TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + "/auth/register", method="POST",
        data=json.dumps({"username": email, "password": "Test123456", "code": "123456"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read())["token"]
    print("QA account:", email)

    # A. 搭链拥齐槽位后运行
    msg = ("帮我搭一条完整MV生产线（歌词 → 依词封面 → MV）：主题《雨夜归途》；"
           "封面要「雨夜窗边的剪影」；MV 要「夜晚城市街头霓虹拖影」。搭好就运行。")
    evs = sse_chat(msg)
    text = "".join(e.get("content", "") for e in evs if e.get("type") == "content")
    tools = [e.get("tool_name") for e in evs if e.get("type") == "tool"]
    snaps = [e for e in evs if e.get("type") == "workflow_snapshot"]
    expect(bool(snaps), "A. 画布快照下发")
    expect(any("add_workflow_chains" in (t or "") for t in tools), f"A. 调了构图工具（{tools}）")
    d = req("GET", "/workflows/draft")
    chains = d["draft"].get("chains", [])
    caps = [n["capability_id"] for n in d["draft"]["nodes"] if n.get("capability_id")]
    expect(bool(chains), f"A. 链注册表非空（labels={[c.get('label') for c in chains]}）")
    expect(set(caps) == {"lyrics.generate", "image.generate", "video.generate"}, f"A. 三块能力齐（{caps}）")
    print("  A 回复:", text[:100].replace("\n", " "))

    p0 = req("GET", "/workflows/run-preview")
    expect(p0["total_cost_credits"] == 10.0, f"B. 预览总额 3+2+5=10（实际 {p0['total_cost_credits']}）")

    if any("run" in (t or "") for t in tools):
        lst = req("GET", "/workflows/runs")
        sub = lst["runs"][0]
    else:
        sub = req("POST", "/workflows/run")
    run_id = sub["run_id"]
    print(f"  B/C 运行: {run_id}")

    t_end = time.time() + 480
    detail = None
    while time.time() < t_end:
        detail = req("GET", f"/workflows/runs/{run_id}")
        if detail["status"] in ("succeeded", "failed", "cancelled"): break
        time.sleep(4)
    assert detail, "unreachable"
    expect(detail["status"] in ("succeeded", "failed", "cancelled"), f"C. 运行收口（{detail['status']}）")

    steps = {s["capability_id"]: s for s in detail["steps"]}
    expect(steps["lyrics.generate"]["status"] in ("succeeded", "reused"),
           f"C1 歌词腿（{steps['lyrics.generate']['status']}）")
    # 上游账户面事件（kunbot 7sref key 401 / SiliconFlow 按户 402）按现状合法预期如实失败；
    # 上游恢复后这里期待转 succeeded/reused——这是验收能区分「平台失败 vs 外部账户失败」的证据位。
    print("  C2 图像腿:", steps["image.generate"]["status"], steps["image.generate"].get("error"))
    print("  C3 视频腿:", steps["video.generate"]["status"], steps["video.generate"].get("error"))

    # D. 复用预览：成功的腿已在缓存里，重新报价只该出失败腿的费
    p1 = req("GET", "/workflows/run-preview")
    states = {b["node_id"]: b["state"] for c in p1["chains"] for b in c["blocks"]}
    lyr_ok = next(s for n, s in states.items() if "lyrics" in n)
    img_ok = next(s for n, s in states.items() if "image" in n)
    vid_ok = next(s for n, s in states.items() if "video" in n)
    expect(lyr_ok == "reused" and vid_ok in ("reused", "cold"), f"D1 成功率腿复用/未成功腿冷（{states}）")
    expect(p1["total_cost_credits"] < 10.0, f"D2 总价不重复全额（实际 {p1['total_cost_credits']}）")

    # E. 档案馆分组
    lst = req("GET", "/workflows/runs")
    found = next((r for r in lst["runs"] if r["run_id"] == run_id), None)
    expect(bool(found), "E. 档案馆含本次")
    if found:
        for blk in found["chains"][0]["blocks"]:
            print(f"    E {blk['node_name']}: {blk['status']}"
                  + (" [reused]" if blk["reused"] else "")
                  + (f" err={blk['error']}" if blk["error"] else ""))
finally:
    api.terminate(); wk.terminate()
    print(f"\n=== MV 生产线验收收尾：{len(FAILED)} FAIL ===")
    if FAILED: print("失败清单:", FAILED)
    sys.exit(1 if FAILED else 0)
