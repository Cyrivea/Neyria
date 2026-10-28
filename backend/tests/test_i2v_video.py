"""I2V（图生视频）测试：schema、适配器打参、属主校验、引擎自动识别、模板注入。"""

import pytest

import services.capabilities.comfyui_video as cv
import services.capabilities.runtime as rt
from schemas.capability import VideoGenerateInput
from services.workflow_service import _resolve_params
from services.workflow_templates import (
    UPSTREAM_ASSET_REF,
    WORKFLOW_TEMPLATES,
    build_chain_params,
    describe_templates,
)


def _video_inputs(**kw):
    data = {"prompt": "镜头缓缓推近", "style": "电影写实", "ratio": "16:9", "duration": "5 秒"}
    data.update(kw)
    return VideoGenerateInput(**data)


# ---------- schema ----------

def test_source_asset_optional_and_validated():
    assert VideoGenerateInput(prompt="动一下").source_asset is None
    assert VideoGenerateInput(prompt="动一下", source_asset="/api/assets/x.jpg").source_asset.endswith("x.jpg")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        VideoGenerateInput(prompt="动一下", source_asset="ab")  # min_length=4


# ---------- runtime：资产归一与属主 ----------

def test_normalize_source_asset_accepts_id_and_url(monkeypatch):
    called = []
    from repositories import asset_repo

    row = {"id": "asset-abc123000000", "kind": "image", "filename": "x.jpg", "user_id": 7}
    monkeypatch.setattr(asset_repo, "get_owned", lambda aid, uid: (called.append(("id", aid, uid)) or (row if (aid, uid) == ("asset-abc123000000", 7) else None)))
    monkeypatch.setattr(asset_repo, "get_owned_by_filename", lambda fn, uid: (called.append(("fn", fn, uid)) or (row if (fn, uid) == ("x.jpg", 7) else None)))

    assert rt._normalize_source_asset("asset-abc123000000", 7) is row
    assert rt._normalize_source_asset("/api/assets/x.jpg", 7) is row
    # 视频资产不可当首帧
    row_video = dict(row, kind="video")
    monkeypatch.setattr(asset_repo, "get_owned", lambda aid, uid: row_video)
    assert rt._normalize_source_asset("asset-abc123000000", 7) is None


def test_video_executor_raises_for_bad_source(monkeypatch):
    from repositories import asset_repo

    monkeypatch.setattr(asset_repo, "get_owned", lambda *a, **kw: None)
    monkeypatch.setattr(asset_repo, "get_owned_by_filename", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="来源图片"):
        rt._video_executor(_video_inputs(source_asset="/api/assets/nobody.jpg"))


def test_video_executor_siliconflow_i2v_honest_reject(monkeypatch):
    from repositories import asset_repo

    monkeypatch.setattr(rt.config, "VIDEO_PROVIDER", "siliconflow")
    monkeypatch.setattr(
        asset_repo, "get_owned",
        lambda aid, uid: {"id": aid, "kind": "image", "filename": "a.jpg"},
    )
    # 预置 of assets 是图像则硅基分支必须拒，而不是静默跑 t2v
    from services.capabilities import CapabilityContext, use_capability_context

    with (
        use_capability_context(CapabilityContext(user_id=1, project_id=None, source="standalone")),
        pytest.raises(RuntimeError, match="不支持图生视频"),
    ):
        rt._video_executor(_video_inputs(source_asset="asset-whatever0000"))


# ---------- 引擎：视频节点自动识别上游图像资产 ----------

def _draft_with(image_candidates, selected=None):
    image_node = {
        "id": "img1",
        "capability_id": "image.generate",
        "params": {"prompt": "封面"},
        "candidates": image_candidates,
    }
    if selected:
        image_node["selectedCandidateId"] = selected
    video_node = {
        "id": "vid1",
        "capability_id": "video.generate",
        "params": {"prompt": "镜头推进", "style": "电影写实", "ratio": "16:9", "duration": "5 秒"},
    }
    return {
        "nodes": [image_node, video_node],
        "edges": [{"source": "img1", "target": "vid1"}],
    }


def test_video_node_auto_i2v_from_upstream_image():
    draft = _draft_with([
        {"id": "c1", "status": "succeeded", "content": "![图](/api/assets/a.png)", "asset_id": "asset-aaa111"},
    ])
    resolved, err = _resolve_params(draft["nodes"][1], draft, {})
    assert err is None
    assert resolved["source_asset"] == "asset-aaa111"


def test_video_node_multi_version_requires_select():
    draft = _draft_with([
        {"id": "c1", "status": "succeeded", "content": "a", "asset_id": "asset-1"},
        {"id": "c2", "status": "succeeded", "content": "b", "asset_id": "asset-2"},
    ])
    resolved, err = _resolve_params(draft["nodes"][1], draft, {})
    assert err == "上游已产出多个版本，请先在其中选一个，再运行当前节点"

    draft["nodes"][0]["selectedCandidateId"] = "c2"
    resolved, err = _resolve_params(draft["nodes"][1], draft, {})
    assert err is None and resolved["source_asset"] == "asset-2"


def test_video_node_upstream_image_empty_fails_honestly():
    draft = _draft_with([])
    _resolved, err = _resolve_params(draft["nodes"][1], draft, {})
    assert err == "上游节点还没有可用产出，请先确保上游节点运行成功后再运行本节点"


def test_explicit_asset_ref_substituted():
    draft = _draft_with([
        {"id": "c1", "status": "succeeded", "content": "x", "asset_id": "asset-zzz9"},
    ])
    draft["nodes"][1]["params"]["source_asset"] = UPSTREAM_ASSET_REF
    resolved, err = _resolve_params(draft["nodes"][1], draft, {})
    assert err is None and resolved["source_asset"] == "asset-zzz9"


# ---------- 模板库 ----------

def test_new_templates_registered_and_documented():
    ids = {t["template_id"] for t in describe_templates()}
    assert "image_anim" in ids and "cover_mv" in ids
    assert len(WORKFLOW_TEMPLATES) == 8


def test_cover_mv_video_node_carries_asset_ref():
    params_list, err = build_chain_params(
        WORKFLOW_TEMPLATES["cover_mv"],
        {
            "theme": "海边", "cover_prompt": "日落帆影", "mv_prompt": "镜头环绕",
        },
    )
    assert err is None
    video_params = params_list[2]
    assert video_params["source_asset"] == UPSTREAM_ASSET_REF


def test_image_anim_requires_source_asset():
    params_list, err = build_chain_params(
        WORKFLOW_TEMPLATES["image_anim"],
        {"anim_prompt": "让云动起来"},
    )
    assert params_list is None and "source_asset" in err
    params_list, err = build_chain_params(
        WORKFLOW_TEMPLATES["image_anim"],
        {"anim_prompt": "让云动起来", "source_asset": "/api/assets/abc.jpg"},
    )
    assert err is None
    assert params_list[0]["source_asset"] == "/api/assets/abc.jpg"


def test_run_capability_result_carries_asset_id(monkeypatch):
    """产资资产能力的返回值必须带 asset_id（下游 I2V 引用在引擎层只认它）。"""
    import asyncio
    import uuid

    from services.capabilities import CapabilityContext, run_capability
    from services.capabilities.runtime import CapabilityEntry

    fake_executor = lambda inputs: {  # noqa: E731  测试桩用 lambda 最直白
        "content": "![图](/api/assets/x.png)",
        "asset": {"asset_id": "asset-abc", "filename": "x.png"},
    }
    entry = CapabilityEntry(
        capability_id="image.generate",
        display_name="图",
        input_model=__import__("schemas.capability", fromlist=["ImageGenerateInput"]).ImageGenerateInput,
        executor=fake_executor,
        billing_model=lambda: "m",
        billing_estimate=lambda: __import__("decimal").Decimal("0"),
    )
    monkeypatch.setitem(rt.CAPABILITY_REGISTRY, "image.generate", entry)  # 自动还原，不泄漏
    monkeypatch.setattr(rt.config, "BILLING_MODE", "off")
    # skip async asset writer side-effect
    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(rt, "_write_asset", _noop)
    monkeypatch.setattr(rt, "_record_usage", _noop)

    res = asyncio.run(
        run_capability(
            "image.generate",
            {"prompt": "x"},
            context=CapabilityContext(user_id=1, project_id=None, source="standalone"),
            idempotency_key="k-" + uuid.uuid4().hex,
        )
    )
    assert res.status == "succeeded"
    assert res.result["asset_id"] == "asset-abc"


# ---------- 适配器 I2V 打参（mock 快门） ----------

def test_i2v_workflow_patches(monkeypatch, tmp_path):
    # 造一个伪 PNG 源图（17x9 头）作资产
    import struct

    # PNG 骨骼：magic(8) + IHDR 块长(4=13) + 'IHDR'(4) + 宽(4)+高(4) 。
    png_head = (
        b"\x89PNG\r\n\x1a\x0a" + struct.pack(">I", 13) + b"IHDR"
        + struct.pack(">II", 700, 393) + b"\x08\x06\x00\x00\x00"
    )
    asset_file = tmp_path.parent / "assets" / "src.png"
    asset_file.parent.mkdir(parents=True, exist_ok=True)
    asset_file.write_bytes(png_head)

    monkeypatch.setattr(cv.config, "COMFYUI_BASE_URL", "http://comfy.test:8188")
    monkeypatch.setattr(cv.config, "DOCUMENT_STORAGE_DIR", tmp_path)

    row = {"id": "asset-1", "kind": "image", "filename": "src.png", "content_type": "image/png"}
    captured: dict = {}

    class _R:
        status_code = 200

        def raise_for_status(self):
            return None

    def fake_post(url, **kw):
        if url.endswith("/upload/image"):
            return type("U", (), {"status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: {"name": "neyria-uploaded.png"}})()
        captured["workflow"] = kw["json"]["prompt"]
        return type("P", (), {"status_code": 200, "raise_for_status": lambda s: None, "json": lambda s: {"prompt_id": "pid-9"}})()

    def fake_get(url, **kw):
        if "/history/" in url:
            return type("H", (), {"status_code": 200, "raise_for_status": lambda s: None,
                                  "json": lambda s: {"pid-9": {"status": {"status_str": "success", "completed": True, "messages": []},
                                                              "outputs": {"14": {"videos": [{"filename": "o.mp4", "subfolder": "video", "type": "output"}]}}}}})()
        return type("V", (), {"status_code": 200, "content": b"mp4-bytes", "raise_for_status": lambda s: None})()

    monkeypatch.setattr(cv.httpx, "post", fake_post)
    monkeypatch.setattr(cv.httpx, "get", fake_get)
    monkeypatch.setattr(cv.time, "sleep", lambda s: None)

    out = cv.generate_video(_video_inputs(), source_asset_row=row)
    wf = captured["workflow"]
    # 节点结构核实：LoadImage 拿到上传名、ImgToVideo 宽高按源图 700x393 等比缩到 32 格
    assert wf["6"]["class_type"] == "LoadImage"
    assert wf["6"]["inputs"]["image"] == "neyria-uploaded.png"
    assert wf["15"]["class_type"] == "LTXVImgToVideo"
    # 源图最长边 700 < 768 不放大，只落 32 格：snap32(700)=672 / snap32(393)=384
    assert wf["15"]["inputs"]["width"] == 672
    assert wf["15"]["inputs"]["height"] == 384
    assert wf["15"]["inputs"]["length"] == 121
    assert "动一下" not in out["content"]  # 输出是 mp4 链接
    assert out["usage"]["model"] == cv.config.VIDEO_MODEL_COMFYUI


# ---------- 链尾续接（B33 第五动词）与节点 id 兜底 ----------

def _make_chain_draft():
    return {
        "nodes": [
            {"id": "workflow-input", "kind": "endpoint"},
            {"id": "lyr1", "capability_id": "lyrics.generate", "x": 300, "y": 280, "params": {"theme": "x"}},
            {"id": "img1", "capability_id": "image.generate", "x": 620, "y": 280, "params": {"prompt": "y"},
             "candidates": [{"id": "c1", "status": "succeeded", "content": "![图](/api/assets/src.png)", "asset_id": "asset-src1"}]},
            {"id": "workflow-output", "kind": "endpoint"},
        ],
        "edges": [
            {"source": "workflow-input", "target": "lyr1"},
            {"source": "lyr1", "target": "img1"},
            {"source": "img1", "target": "workflow-output"},
        ],
        "chains": [
            {"chain_no": 2, "chain_id": "ch-2", "label": "词+图", "template_id": "lyrics_cover",
             "spec_hash": "h", "node_ids": ["lyr1", "img1"], "source": "assistant"},
        ],
    }


def test_extend_appends_and_rewires_edges(monkeypatch):
    from services.workflow_service import extend_workflow_chain

    monkeypatch.setattr("services.workflow_service._check_project_owner", lambda *a: None)
    monkeypatch.setattr("services.workflow_service._ensure_canvas_read", lambda *a: None)
    # 用假 draft 走 mutate 分支（CAS 层被桩掉）
    monkeypatch.setattr("services.workflow_service._mutate_draft_with_cas",
                        lambda u, p, fn: (fn(_d := _make_chain_draft()) or None) or {"id": 1, "revision": 2, "draft": _d})
    out = extend_workflow_chain(1, None, chain_no=2, capability_or_type="video",
                                params={"prompt": "镜头缓慢推近", "duration": "5 秒"})
    draft = out["draft"]
    ext = out["extension"]
    assert ext["appended"]["capability"] == "video.generate"
    new_id = ext["appended"]["node_id"]
    edge_set = {(e["source"], e["target"]) for e in draft["edges"]}
    assert ("img1", "workflow-output") not in edge_set
    assert ("img1", new_id) in edge_set
    assert (new_id, "workflow-output") in edge_set
    chain = draft["chains"][0]
    assert new_id in chain["node_ids"] and chain["spec_hash"] is None


def test_extend_unknown_chain_or_dirty_tail(monkeypatch):
    from core.exceptions import AppError
    from services.workflow_service import extend_workflow_chain

    monkeypatch.setattr("services.workflow_service._check_project_owner", lambda *a: None)
    monkeypatch.setattr("services.workflow_service._ensure_canvas_read", lambda *a: None)
    monkeypatch.setattr("services.workflow_service._mutate_draft_with_cas",
                        lambda u, p, fn: (fn(_d := _make_chain_draft()) or None) or {"id": 1, "revision": 2, "draft": _d})
    with pytest.raises(AppError, match="找不到链"):
        extend_workflow_chain(1, None, chain_no=99, capability_or_type="video", params={"prompt": "x"})


def test_node_id_source_asset_resolved_by_engine(monkeypatch):
    draft = _make_chain_draft()
    draft["nodes"].append({
        "id": "vid-new", "capability_id": "video.generate",
        "params": {"prompt": "推近", "style": "电影写实", "ratio": "16:9", "duration": "5 秒",
                   "source_asset": "img1"},  # 模型塞了节点 id（原始形态可能是 workflow-node-img1）
        "candidates": [],
    })
    draft["edges"].extend([
        {"source": "workflow-input", "target": "vid-new"},
        {"source": "vid-new", "target": "workflow-output"},
    ])
    vid = draft["nodes"][-1]
    vid["params"]["source_asset"] = "workflow-node-img1"  # 模拟模型误传
    # img1 在本 draft 里不带 workflow-node- 前缀，引擎按 id 全文匹配；先把 id 补成带前缀场景
    draft["nodes"][2]["id"] = "workflow-node-img1"
    draft["edges"] = [{"source": e["source"] if e["source"] != "img1" else "workflow-node-img1",
                       "target": e["target"] if e["target"] != "img1" else "workflow-node-img1"} for e in draft["edges"]]
    resolved, err = _resolve_params(vid, draft, {})
    assert err is None
    assert resolved["source_asset"] == "asset-src1"  # 引擎兜底把节点 id 换算成真实资产


def test_node_id_source_asset_without_output_fails():
    draft = _make_chain_draft()
    draft["nodes"][2]["candidates"] = []
    draft["nodes"].append({
        "id": "vid-new", "capability_id": "video.generate",
        "params": {"prompt": "推近", "style": "电影写实", "ratio": "16:9", "duration": "5 秒",
                   "source_asset": "workflow-node-img1"},
        "candidates": [],
    })
    _resolved, err = _resolve_params(draft["nodes"][-1], draft, {})
    assert err == "指定的来源节点还没有可用产出，请先让它运行成功"
