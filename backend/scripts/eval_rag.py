"""RAG 召回率评测（H3 遗留件）：知识库到底查不查得中，给简历留真实数字。

与 eval_intent / eval_injection 同一训练：不评模型嘴，只评检索行为本身——
给定语料库（8 片音乐制作领域文档），问一批有明确答案来源的问题，看 bge-m3 +
Chroma + RAG_DISTANCE_THRESHOLD 三层指导下能不能把对的文档捞到 top-K；

另加一组「库汉那么多」の拷问：与语料完全无关的问题期望零命中（越界阈值拦
载下应空空如也，从而放给 chat 层的联网后备，而不是硬塞一段不相关的片段）。

隔离：临时建一个专属 collection（rag_eval_<pid>），跑完整体删除，生产知识库分
帕不动。和 eval_injection.py 一样是「真机评测」：吃 SILICONFLOW_API_KEY，欠费
402 时诚实报错退出，不伪造数字。

运行（backend/ 下）：
    uv run python scripts/eval_rag.py
判定通过（exit 0）约石：Recall@3 == 1.0 且无关查询零命中 == 1.0（可用
--min-recall / --min-nohit 后拔后拉线）。产出 eval_rag_result.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import RAG_DISTANCE_THRESHOLD
from services.rag import retriever, service
from services.rag.vectorstore import VectorStore

RESULT_PATH = Path(__file__).resolve().parents[1] / "eval_rag_result.json"

# ---------------- 语料库：8 片主题分离清晰的领域文档 ----------------
# 选材原则：全是本项目音乐创作主线的外部资料形态（真会进用户知识库的东西），
# 每片主题浓度高、词互踿少，期望 bge-m3 能轻易分开
CORPUS: dict[str, str] = {
    "chord_progressions.txt": (
        "流行音乐经典和声进行速查。I-V-vi-IV（C-G-Am-F）卡农走向是华语流行与欧美流行的万金油，"
        "G 大调对应 G-D-Em-C。vi-IV-I-V（悲走向）自带叙事感，常用于慢情歌主歌铺垫。"
        "J-Pop 王道进行 IV-V-iii-vi 有很强的推进与诀别感。写副歌时避免与主歌使用同一进行，"
        "常用换调式、倒置和弦或转调来制造张力。小调 i-VI-III-VII 是摇滚与说唱的常见底色。"
    ),
    "song_structure.txt": (
        "曲式结构拆解。主流流行歌以 Intro-Verse-PreChorus-Chorus-Verse-PreChorus-Chorus-"
        "Bridge-Chorus-Outro 为骨架，记作 ABCABCBX。主歌（Verse）负责叙事递进，每段歌词应换新信息；"
        "副歌（Chorus）是记忆点所在，旋律要上扬、歌词要可直接引用传诵；"
        "预副歌（Pre-Chorus）承上启下制造期待；桥段（Bridge）在全曲三分之二处提供对比与转折；"
        "无桥段可用间奏 Solo 代替。AABA 曲式见传统民谣与爵士标准曲。"
    ),
    "mixing_eq.txt": (
        "混音 EQ 入门。人声轨道先高通滤波（HPF）去低频轰响，男声切 80-100Hz，女声切 100-120Hz。"
        "200-400Hz 是浑浊区，轻微衰减可提升清晰度；3-5kHz 决定人声的穿透与咬字，提升会让声音靠前；"
        "8-12kHz 增加空气感，过量则齿音刺耳，需要 De-esser 压制。伴奏轨道与人声频段冲突时，"
        "在伴奏侧为 3kHz 附近做 1-2dB 的侧链凹陷，让人声永远站在最前面。"
    ),
    "lyrics_rhyme.txt": (
        "中文作词押韵要点。普通话十三辙是押韵的基本功：发花（a）、梭波（o/e）、乜斜（ê/ie）、"
        "一七（i/ü）、姑苏（u）、怀来（ai）、灰堆（ei）、遥条（ao）、油求（ou）、言前（an）、"
        "人辰（en）、江阳（ang）、中东（eng/ong）。副歌应使用开口度大的韵脚（江阳、发花）以利传唱；"
        "叙事段可用细韵（一七、青菜）。偶句押韵比句句押韵更耐听，避免为了押韵硬凑病句，"
        "语义永远优先于韵脚。"
    ),
    "vocal_recording.txt": (
        "人声录制实操。录音室首选大振膜电容麦克风，动圈麦抗噪更强适合家用环境。"
        "防喷罩必须安装，麦克风距离保持 15-25cm，避免 5cm 内的近讲效应让低频堆积。"
        "房间没有声学装修时用被子或衣橱作为简易吸声，床帘围出半封闭录音棚是低成本方案。"
        "录制电平峰顶瞄准 -12dB 到 -6dB，宁可偏小不可爆音；同一句歌词录 3-5 轨后做 Comping 拼接。"
        "监听耳机闭合式优于开放式，漏音会污染人声轨。"
    ),
    "daw_workflow.txt": (
        "DAW 工程管理习惯。新建工程第一件事是设定采样率 48kHz / 24bit，并统一项目文件命名规范"
        "（歌名_版本_日期）。轨道要分组编组（Drums/Bass/Keys/Vox/BGV），每轨挂色标以便现场快速定位。"
        "常用快捷键：Ctrl/Cmd+S 保存、Cmd+Z 撤销、空格走带、B 换笔刷工具。"
        "每个重大方向修改前先保存版本快照，绝不在单文件上反复覆盖；定期冻结高 CPU 的音源轨，"
        "避免大型工程后期卡顿丢音。"
    ),
    "mastering_lufs.txt": (
        "流媒体时代的母带响度标准。主流平台采用 LUFS 单位做响度归一化：网易云与 QQ 音乐目标约 "
        "-14 LUFS，Spotify 同样 -14 LUFS，Apple Music 约 -16 LUFS。母带峰值保留 -1dBTP 真实峰值"
        "限幅防止转码削波。动态范围（Dynamics）不应小于 -8 dB，纯靠 Loudness War 压出的砖墙母带"
        "会被平台降回调到合理响度，却永久损失动态与细节。限幅器（Limiter）前应先做压缩、再做 EQ 微调。"
    ),
    "copyright_licence.txt": (
        "音乐版权与授权基础。词曲著作权与录音制品权是两个不同权利，翻唱只需词曲授权。"
        "采未授权样本（采样）发布商用作品会被原创权利人追诉，甚至是公版旋律的录音版本也受保护。"
        "Creative Commons（CC BY-NC 等）只能按其条款免费使用，商用仍需单独取得许可。"
        "AI 生成音乐的版权归属在各国尚无统一立法，商业使用前应阅读平台的用户协议，"
        "重要作品可考虑在中国版权保护中心登记词曲版权以固证权。"
    ),
}

# ---------------- 查询集：每条有明确期望命中的文档文件名 ----------------
# 原则：不从语料原文里抄关键词原句，而是换个说法向语义提问——核查真语义匹配
CASES: list[dict] = [
    {"query": "情歌和弦怎么配才有那种推进的感觉", "expect": "chord_progressions.txt"},
    {"query": "主歌和副歌应该各写什么内容", "expect": "song_structure.txt"},
    {"query": "一首歌中间差不多三分之二的地方应该来点什么变化", "expect": "song_structure.txt"},
    {"query": "人声听起来很沉不够穿透是哪里出了问题", "expect": "mixing_eq.txt"},
    {"query": "副歌应该选什么样的韵脚最容易记住", "expect": "lyrics_rhyme.txt"},
    {"query": "中文歌词最后一字用「江」「阳」「刚」这些字算合辙吗", "expect": "lyrics_rhyme.txt"},
    {"query": "家里没有录音棚怎么隔绝外面的噪音录人声", "expect": "vocal_recording.txt"},
    {"query": "麦克风唛噗噗的爆喷音怎么避免", "expect": "vocal_recording.txt"},
    {"query": "做完一半想尝试另一种编曲方向怕改不回来了怎么办", "expect": "daw_workflow.txt"},
    {"query": "CPU 不够了大工程跑不开声音卡顿", "expect": "daw_workflow.txt"},
    {"query": "上传到音乐平台的歌响度应该调多少才不会被压", "expect": "mastering_lufs.txt"},
    {"query": "别人翻唱我的歌我要拿着什么权利收钱", "expect": "copyright_licence.txt"},
    # ---- 越界挡板：库汉那么多问题，期望零命中 ----
    {"query": "红烧肉怎么做", "expect": None},
    {"query": "北京到上海的驾车时间要几点能到", "expect": None},
    {"query": "请推荐几本量子力学基础教材", "expect": None},
]

MAX_N_RESULTS = 3  # 与生产 chat 链路一致（search_with_metadata n_results=3）


def build_eval_store(name: str) -> VectorStore:
    """临时专属 collection，跑完整体删，生产知识库零波及。"""
    store = VectorStore(collection_name=name)
    return store


def ingest_corpus(store: VectorStore, tmp_dir: Path) -> None:
    """走真实生产路径：写临时 .txt → service.add_document（切分+批量 embedding+索引）。"""
    for filename, text in CORPUS.items():
        file_path = tmp_dir / filename
        file_path.write_text(text, encoding="utf-8")
        service.add_document(
            file_path=str(file_path),
            filename=filename,
            scope="assistant",
        )


def run_cases() -> list[dict]:
    details = []
    for case in CASES:
        expected = case["expect"]
        hits = service.search_with_metadata(
            case["query"],
            n_results=MAX_N_RESULTS,
            scope="assistant",
        )
        ranked_sources = [hit.get("source", "") for hit in hits]
        if expected is None:
            ok = len(hits) == 0
            rank = None
        else:
            rank = None
            for idx, src in enumerate(ranked_sources):
                if src == expected:
                    rank = idx + 1
                    break
            ok = rank is not None
        details.append(
            {
                "query": case["query"],
                "expect": expected,
                "hits": [
                    {"source": src, "distance": round(hit.get("distance", -1), 4)}
                    for src, hit in zip(ranked_sources, hits, strict=False)
                ],
                "first_rank": rank,
                "ok": ok,
            }
        )
        flag = "✅" if ok else "❌"
        kind = "检索" if expected else "越界"
        rank_text = (
            f"{len(hits)} hits" if expected is None else (f"rank={rank}" if rank else "missed")
        )
        print(f"{flag} [{kind}] {case['query']} → {rank_text}")
    return details


def summarize(details: list[dict]) -> dict:
    positive = [d for d in details if d["expect"] is not None]
    neg = [d for d in details if d["expect"] is None]
    recall1 = sum(1 for d in positive if d["first_rank"] == 1) / max(1, len(positive))
    recall3 = sum(1 for d in positive if d["first_rank"] is not None) / max(1, len(positive))
    rr = sum(1.0 / d["first_rank"] for d in positive if d["first_rank"]) / max(1, len(positive))
    nohit = sum(1 for d in neg if d["ok"]) / max(1, len(neg))
    return {
        "total": len(details),
        "positive_cases": len(positive),
        "negative_cases": len(neg),
        "recall_at_1": round(recall1, 4),
        "recall_at_3": round(recall3, 4),
        "mrr": round(rr, 4),
        "nohit_accuracy": round(nohit, 4),
        "max_n_results": MAX_N_RESULTS,
        "distance_threshold": RAG_DISTANCE_THRESHOLD,
        "corpus_size": len(CORPUS),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-recall", type=float, default=1.0, help="Recall@3 合格线（默认满中）")
    ap.add_argument("--min-nohit", type=float, default=1.0, help="无关查询零命中率合格线")
    ap.add_argument("--keep", action="store_true", help="跑完不删 collection（调试用）")
    args = ap.parse_args()

    coll_name = f"rag_eval_{os.getpid()}"
    store = build_eval_store(coll_name)
    # production service 模块全局只有一个 vectorstore 实例：本脚本全进程独占，
    # 原地换到专属 collection，跑完在 finally 里删掉
    original = retriever.vectorstore
    retriever.vectorstore = store
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="rag_eval_corpus_") as tmp:
            print(f"[ingest] 语料 {len(CORPUS)} 片 → collection {coll_name}")
            ingest_corpus(store, Path(tmp))
        print(f"[eval] 查询 {len(CASES)} 条（含 {sum(1 for c in CASES if c['expect'] is None)} 条期望零命中的无关查询，distance 阈值={RAG_DISTANCE_THRESHOLD}）")
        details = run_cases()
    finally:
        retriever.vectorstore = original
        if not args.keep:
            store.client_db.delete_collection(coll_name)

    summary = summarize(details)
    summary["elapsed_s"] = round(time.perf_counter() - started, 1)
    RESULT_PATH.write_text(
        json.dumps({"summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\n===== 汇总 =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"写入 {RESULT_PATH.name}")

    passed = (
        summary["recall_at_3"] >= args.min_recall
        and summary["nohit_accuracy"] >= args.min_nohit
    )
    print(f"\n判定：{'PASS' if passed else 'FAIL'}（要求 Recall@3≥{args.min_recall} / nohit≥{args.min_nohit}）")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
