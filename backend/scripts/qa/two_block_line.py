"""三个 Block 现状验收（MV 腿暂缺）： ① 歌词单跑 ② 图像单跑 ③ 歌词→封面联动链。

用法：cd backend && uv run python scripts/qa/two_block_line.py
"""
import json, random, string, subprocess, sys, time, urllib.request

ROOT = "/home/cyrivea/Neyria/backend"
BASE = "http://127.0.0.1:18790"

api = subprocess.Popen(["./.venv/bin/python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "18790"],
    cwd=ROOT, stdout=open("/tmp/qa-2b-api.log", "w"), stderr=subprocess.STDOUT)
wk = subprocess.Popen(["./.venv/bin/python", "-m", "workers.job_worker"],
    cwd=ROOT, stdout=open("/tmp/qa-2b-wk.log", "w"), stderr=subprocess.STDOUT)

FAILED = []
def expect(c, l):
    print(("PASS " if c else "FAIL ") + l)
    if not c: FAILED.append(l)

def req(m, p, b=None, t=240):
    r = urllib.request.Request(BASE + p, method=m,
        data=json.dumps(b).encode() if b is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=t).read() or b"{}")

def wait_run(run_id, budget=420):
    t_end = time.time() + budget
    while time.time() < t_end:
        d = req("GET", f"/workflows/runs/{run_id}")
        if d["status"] in ("succeeded", "failed", "cancelled"):
            return d
        time.sleep(4)
    return None

TOKEN = ""
try:
    for _ in range(90):
        try: urllib.request.urlopen(BASE + "/health", timeout=2); break
        except Exception: time.sleep(0.5)
    import redis as _r
    email = "qa-2b-" + "".join(random.choices(string.ascii_lowercase, k=6)) + "@example.com"
    r = _r.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    r.hset(f"authcode:{email}", mapping={"code": "123456", "attempts": 0}); r.expire(f"authcode:{email}", 600)
    TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/auth/register", method="POST",
        data=json.dumps({"username": email, "password": "Test123456", "code": "123456"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read())["token"]
    print("QA account:", email)

    def set_chain(nodes_def, edges_def, revision_key="current"):
        d = req("GET", "/workflows/draft")
        d["draft"]["nodes"] = [
            {"id": "workflow-input", "kind": "endpoint", "endpoint": "input", "name": "输入", "x": 36, "y": 280},
            *nodes_def,
            {"id": "workflow-output", "kind": "endpoint", "endpoint": "output", "name": "输出", "x": 900, "y": 280},
        ]
        d["draft"]["edges"] = edges_def
        req("PUT", "/workflows/draft", {"project_id": None, "expected_revision": d["revision"], "draft": d["draft"]})

    # ======== 一、歌词单块 ========
    set_chain(
        [{"id": "n-lyr", "kind": "tool", "type": "lyrics", "capability_id": "lyrics.generate",
          "name": "写歌词", "params": {"theme": "凌晨咖啡馆"}, "x": 300, "y": 280}],
        [{"source": "workflow-input", "target": "n-lyr"}, {"source": "n-lyr", "target": "workflow-output"}],
    )
    sub = req("POST", "/workflows/run")
    detail = wait_run(sub["run_id"])
    expect(detail and detail["status"] == "succeeded", f"① 歌词单块（{detail and detail['status']}）")
    lst = req("GET", "/workflows/runs")
    rec = next(r for r in lst["runs"] if r["run_id"] == sub["run_id"])
    lyr_text = next((b.get("content", "") for c in rec["chains"] for b in c["blocks"] if b["capability_id"]=="lyrics.generate"), "")
    expect(bool(lyr_text) and len(lyr_text) > 80, f"① 歌词不是空话（{len(lyr_text or '')} 字）")
    print("   ① 歌词首部:", (lyr_text or "")[:80].replace("\n", " "))

    # ======== 二、图像单块（独立 prompt，不接上游） ========
    set_chain(
        [{"id": "n-img", "kind": "tool", "type": "image", "capability_id": "image.generate",
          "name": "封面", "params": {"prompt": "一只戴耳机的灰色猫，坐在极简办公桌前"}, "x": 300, "y": 280}],
        [{"source": "workflow-input", "target": "n-img"}, {"source": "n-img", "target": "workflow-output"}],
    )
    sub = req("POST", "/workflows/run")
    detail = wait_run(sub["run_id"], budget=300)
    expect(detail and detail["status"] == "succeeded", f"② 图像单块（{detail and detail['status']}）")
    lst = req("GET", "/workflows/runs")
    rec = next(r for r in lst["runs"] if r["run_id"] == sub["run_id"])
    img_c = next((b.get("content", "") for c in rec["chains"] for b in c["blocks"] if b["capability_id"]=="image.generate"), "")
    expect("/api/assets/" in (img_c or ""), f"② 真的有资产（{(img_c or '')[:70]}）")

    # ======== 三、歌词→依词封面 联动（升级链） ========
    set_chain(
        [
            {"id": "n-lyr", "kind": "tool", "type": "lyrics", "capability_id": "lyrics.generate",
             "name": "写歌词", "params": {"theme": "雨后便利店"}, "x": 300, "y": 280},
            {"id": "n-img", "kind": "tool", "type": "image", "capability_id": "image.generate",
             "name": "封面", "params": {"prompt": "${upstream.result.content} 专辑封面，极简插画"}, "x": 600, "y": 280},
        ],
        [
            {"source": "workflow-input", "target": "n-lyr"},
            {"source": "n-lyr", "target": "n-img"},
            {"source": "n-img", "target": "workflow-output"},
        ],
    )
    p0 = req("GET", "/workflows/run-preview")
    expect(p0["ready"] and p0["total_cost_credits"] == 5.0, f"③ 预览计价 3+2=5（实际 {p0['total_cost_credits']}）")
    sub = req("POST", "/workflows/run")
    detail = wait_run(sub["run_id"], budget=420)
    expect(detail and detail["status"] == "succeeded", f"③ 联动链全通（{detail and detail['status']}）")
    steps = {s["capability_id"]: s for s in detail["steps"]}
    expect(steps["lyrics.generate"]["status"] == "succeeded", f"③ 歌词腿（{steps['lyrics.generate']['status']}）")
    expect(steps["image.generate"]["status"] == "succeeded", f"③ 封面腿（{steps['image.generate'].get('error') or 'succeeded'}）")

    # 复用预览：歌词没问题参数没改，该复用；封面不变
    p1 = req("GET", "/workflows/run-preview")
    states = {b["node_id"]: b["state"] for c in p1["chains"] for b in c["blocks"]}
    print("  ③ 复用预览:", states, "| 总价:", p1["total_cost_credits"])
    expect(all(s == "reused" for s in states.values()), "③ 全链复用命中")

    # 档案馆里三次运行都归档了
    lst = req("GET", "/workflows/runs")
    expect(len(lst["runs"]) == 3, f"④ 三次运行都在档案馆（实际 {len(lst['runs'])}）")
finally:
    api.terminate(); wk.terminate()
    print(f"\n=== 三块全链验收收尾：{len(FAILED)} FAIL ===")
    if FAILED: print("失败清单:", FAILED)
    sys.exit(1 if FAILED else 0)
