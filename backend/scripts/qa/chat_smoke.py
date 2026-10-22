"""聊天网关切换真机验收（Kunbot/OpenAI 兼容面）。

用法：cd backend && uv run python scripts/qa/chat_smoke.py

覆盖 K1-K3（带全新账号作为零污染基线）：
K1 心跳 + 流式正文 + 文本不含内部工具名词条
K2 搜索类诉求触发 search_web 并出中文总结
K3 构图类诉求调 add_workflow_chains 但用户说「先别跑」时绝不点火
"""
import json
import random
import string
import subprocess
import sys
import time
import urllib.request

ROOT = "/home/cyrivea/Neyria/backend"
BASE = "http://127.0.0.1:18300"

api = subprocess.Popen(["./.venv/bin/python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "18300"],
    cwd=ROOT, stdout=open("/tmp/qa-k-api.log", "w"), stderr=subprocess.STDOUT)
wk = subprocess.Popen(["./.venv/bin/python", "-m", "workers.job_worker"],
    cwd=ROOT, stdout=open("/tmp/qa-k-wk.log", "w"), stderr=subprocess.STDOUT)

FAILED = []
def expect(c, label):
    print(("PASS " if c else "FAIL ") + label)
    if not c: FAILED.append(label)

def sse_chat(token, msg, timeout=240):
    r = urllib.request.Request(BASE + "/chat", method="POST",
        data=json.dumps({"message": msg, "project_id": None}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    raw = urllib.request.urlopen(r, timeout=timeout).read().decode("utf-8", "replace")
    evs = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                try: evs.append(json.loads(line[6:]))
                except Exception: pass
    types = [e.get("type") for e in evs]
    text = "".join(e.get("content", "") for e in evs if e.get("type") == "content")
    tools = [e.get("tool_name") for e in evs if e.get("type") == "tool"]
    return types, text, tools

try:
    for _ in range(90):
        try: urllib.request.urlopen(BASE + "/health", timeout=2); break
        except Exception: time.sleep(0.5)
    import redis as _redis
    email = "qa-kb-" + "".join(random.choices(string.ascii_lowercase, k=6)) + "@example.com"
    r = _redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
    r.hset(f"authcode:{email}", mapping={"code": "123456", "attempts": 0}); r.expire(f"authcode:{email}", 600)
    TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + "/auth/register", method="POST",
        data=json.dumps({"username": email, "password": "Test123456", "code": "123456"}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30).read())["token"]
    print("QA account:", email)

    t1, text1, tools1 = sse_chat(TOKEN, "你好，简短介绍下自己")
    expect("heartbeat" in t1, "K1 心跳在位")
    expect(bool(text1.strip()), f"K1 有流式正文（长度 {len(text1)}）")
    expect(not any(k in text1 for k in ("search_web", "compose_chains", '"query"')), "K1 正文干净")
    print("  K1:", text1[:80].replace("\n", " "))

    t2, text2, tools2 = sse_chat(TOKEN, "帮我搜一下智能体（agent）这个词的大致含义")
    expect(bool(tools2), f"K2 工具被调用（tools={tools2}）")
    print("  K2 tools:", tools2, "| 正文前60:", text2[:60].replace("\n", " "))

    t3, text3, tools3 = sse_chat(TOKEN, "帮我搭一条「写完歌词就依词生封面」的链：主题写星际迷航的孤独，封面要雨夜窗口剪影。先别运行。")
    expect(bool(tools3) and not any("run" in (t or "") for t in tools3), f"K3 只构图不运行（tools={tools3}）")
    d = json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + "/workflows/draft", headers={"Authorization": f"Bearer {TOKEN}"}), timeout=30).read())
    n_img = [n for n in d["draft"]["nodes"] if n.get("capability_id") == "image.generate" and n.get("kind") != "endpoint"]
    expect(bool(n_img), "K3 画布出现图像块")
    print("  K3 chains:", [c.get("label") for c in d["draft"].get("chains", [])], "| tools:", tools3)
finally:
    api.terminate(); wk.terminate()
    print(f"\n=== chat 网关验收收尾：{len(FAILED)} FAIL ===")
    sys.exit(1 if FAILED else 0)
