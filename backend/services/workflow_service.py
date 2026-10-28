"""Workflow Draft、模板链构图与异步 Runner。

- 构图（WF1-①）：助手只能以模板为单位成链（``add_workflow_chains``），
  连线由平台机械完成（各链共享 Input/Output 端点），多链并行互不干扰。
- 执行：``submit_workflow_run`` 入队 → Worker 跑 ``_execute_snapshot_run``；
  节点级成功快照（resolved 参数指纹）命中即复用上次结果，零调用零计费。
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core import config
from core.exceptions import AppError
from repositories import asset_repo, workflow_repo, workflow_run_repo
from schemas.capability import CapabilityResult
from services import job_service
from services.capabilities import CAPABILITY_REGISTRY, CapabilityContext, run_capability
from services.project_service import get_project
from services.workflow_templates import (
    build_chain_params,
    coalesce_dependency_specs,
    describe_templates,
    get_template,
)

logger = logging.getLogger("workflow")

INPUT_ID = "workflow-input"
OUTPUT_ID = "workflow-output"

# 节点网格：与前端 positionForIndex 同一套坐标（起点 300、列宽 300、最多 3 列后换行）。
# 之前的“x = 320 + 300 * n”不取模，节点一多就淤出画布可视区，实测被压到右侧聊天面板后。
def position_x_for_index(tool_index: int) -> int:
    return 300 + (tool_index % 3) * 300

# “画布已读”印记：同一请求轮内，读过画布的用户/项目才被允许 configure/clear。
# 用 contextvars 保证并发请求互不影响；每轮聊天由编排器重置。
_canvas_read_stamp: contextvars.ContextVar[tuple[int, int | None] | None] = (
    contextvars.ContextVar("canvas_read_stamp", default=None)
)


def reset_canvas_read_stamp() -> None:
    _canvas_read_stamp.set(None)


def mark_canvas_read(user_id: int, project_id: int | None) -> None:
    _canvas_read_stamp.set((user_id, project_id))


def _ensure_canvas_read(user_id: int, project_id: int | None, draft: dict[str, Any]) -> None:
    """"先读再改"的保底实现（GLM 时代模型自觉不可靠）：

    模型没读而动手时，平台代它读一次再放行，并在结果里告知——
    安全性由机器保证，可观测性交给工具描述卷。评测过几次证明，该模型在
    prompt、工具描述、被拒重试三档提醒下依然 0% 主动读，不能让用户感受为瓶颈买单。
    """
    has_content = any(n.get("capability_id") for n in draft.get("nodes", []))
    if not has_content:
        mark_canvas_read(user_id, project_id)
        return
    if _canvas_read_stamp.get() == (user_id, project_id):
        return

    get_workflow_summary(user_id, project_id)
    mark_canvas_read(user_id, project_id)


CAPABILITY_META: dict[str, dict[str, Any]] = {
    "lyrics.generate": {
        "type": "lyrics",
        "name": "歌词生成",
        "badge": "文字创作",
        "description": "根据主题、情绪和曲风生成完整歌词草稿。",
        "color": "#b7b7b7",
    },
    "image.generate": {
        "type": "image",
        "name": "图像生成",
        "badge": "文字生图",
        "description": "把文字描述转成歌曲封面、角色设定和视觉概念图。",
        "color": "#d0d0d0",
    },
    "video.generate": {
        "type": "video",
        "name": "视频生成",
        "badge": "文字生视频",
        "description": "把镜头与画面描述转成短视频片段（文生视频）。",
        "color": "#a6a6a6",
    },
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def empty_draft() -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": INPUT_ID,
                "kind": "endpoint",
                "endpoint": "input",
                "name": "输入",
                "description": "主题、初始文本和参考文件",
                "x": 36,
                "y": 280,
            },
            {
                "id": OUTPUT_ID,
                "kind": "endpoint",
                "endpoint": "output",
                "name": "输出",
                "description": "展示、保存和导出最终结果",
                "x": 1230,
                "y": 280,
            },
        ],
        "edges": [],
        "endpointPositions": {"input": {"x": 36, "y": 280}},
        # 链注册表（WF1-①）：chains=[] 条带号登记、chain_counter 单调不减只增不退——
        # 删链不重排（留空号），防止对话中指代错位导致错删。
        "chains": [],
        "chain_counter": 0,
    }


def get_workflow_summary(user_id: int, project_id: int | None) -> dict[str, Any]:
    """Assistant 读画布的快速摘要；nodes 带 capability_id/type/params，edges 是全量连接。"""
    current = get_draft(user_id, project_id)
    nodes = [
        {
            "id": n.get("id"),
            "kind": n.get("kind"),
            "type": n.get("type"),
            "name": n.get("name"),
            "capability_id": n.get("capability_id"),
            "params": n.get("params"),
            "run_status": n.get("runStatus"),
        }
        for n in current["draft"].get("nodes", [])
    ]
    edges = [
        {"source": e.get("source"), "target": e.get("target")}
        for e in current["draft"].get("edges", [])
    ]
    chain_summary = []
    for entry in _chain_registry(current["draft"]):
        members = _chain_nodes(current["draft"], [str(i) for i in entry.get("node_ids", [])])
        chain_summary.append(
            {
                "chain_no": entry.get("chain_no"),
                "label": entry.get("label"),
                "template_id": entry.get("template_id"),
                "source": entry.get("source"),
                "blocks": [
                    {
                        "node_id": n.get("id"),
                        "type": n.get("type"),
                        "name": n.get("name"),
                        "run_status": n.get("runStatus"),
                        "params": n.get("params"),
                    }
                    for n in members
                ],
            }
        )
    return {
        "revision": current["revision"],
        "workflow_id": current["id"],
        "nodes": nodes,
        "edges": edges,
        "chains": chain_summary,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


def _check_project_owner(user_id: int, project_id: int | None) -> None:
    """默认工作区（project_id=None）允许；指定项目必须属于当前用户。"""
    if project_id is not None and get_project(user_id, project_id) is None:
        raise AppError("项目不存在", status_code=404)


def get_draft(user_id: int, project_id: int | None) -> dict[str, Any]:
    _check_project_owner(user_id, project_id)
    saved = workflow_repo.get(user_id, project_id)
    if saved is None:
        return {"id": None, "revision": 0, "draft": empty_draft()}
    return {"id": saved["id"], "revision": saved["revision"], "draft": saved["draft"]}


_MAX_CAS_RETRIES = 5


def _mutate_draft_with_cas(
    user_id: int,
    project_id: int | None,
    mutate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """唯一合法的 Draft 写通道：读最新 → 业务修改 → CAS 写入，冲突重读重试。

    运行期间 Runner 每节点回写 与 模型/用户中途 configure 必然并发；
    没有乐观锁就是“最后一个整档覆盖的赢”，双节点/掉节点都是这里来的。
    """
    for _ in range(_MAX_CAS_RETRIES):
        current = get_draft(user_id, project_id)
        draft = copy.deepcopy(current["draft"])
        mutate(draft)  # 这里的 raise（如连线护栏）会原样传出，不算冲突
        saved = workflow_repo.upsert_if_revision(
            user_id, project_id, current["revision"], draft, _now()
        )
        if saved is not None:
            return {"id": saved["id"], "revision": saved["revision"], "draft": saved["draft"]}
    raise AppError("画布正在被并发更新，请稍后重试", status_code=409)


def save_draft(
    user_id: int,
    project_id: int | None,
    draft: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    current = get_draft(user_id, project_id)
    if current["revision"] != expected_revision:
        raise AppError("Workflow 已被其他操作更新，请刷新后重试", status_code=409)
    # 运行期间禁止外部编辑：运行快照回写与用户写入并存会互相踩掉对方。
    # 前端有只读锁，这里是兜底；错误码 WORKFLOW_BUSY 供前端区分友好提示。
    # get_active_run 带自愈：job 已终局的僵尸运行会被顺手收尸，不会永远锁住画布。
    if get_active_run(user_id, current["id"]) is not None:
        raise AppError(
            "WORKFLOW_BUSY：Workflow 正在运行，运行结束后会自动解锁，请稍后再编辑",
            status_code=409,
        )
    # B30：没有活跃运行时，客户端不应声称任何节点还在 running——残留的僵尸状态会让
    # 前端误判“运行中”（横幅 + 画布锁 + 无限轮询）。一律降级 idle，状态由下次真实运行重建。
    for node in draft.get("nodes", []):
        if isinstance(node, dict) and node.get("runStatus") == "running":
            node["runStatus"] = "idle"
    _validate_draft_shape(draft)
    # 前端只携带自己认识的字段回存：服务端字段（缓存快照/链编号/注册表）从旧草稿携带，
    # 再把用户手搭的合法直线链补登记（链号从此用户/AI 混编、删后不退号）。
    _carry_forward_server_owned(draft, current["draft"])
    _sync_chain_registry(draft)
    saved = workflow_repo.upsert(user_id, project_id, expected_revision + 1, draft, _now())
    return {"id": saved["id"], "revision": saved["revision"], "draft": saved["draft"]}


def _drop_orphan_running(draft: dict[str, Any], executing_ids: set[str]) -> None:
    """把不在本次执行序列中的僵尸 running 归为 idle（B30 保险丝）。

    runStatus 只有“被本次运行真正执行”的节点才允许是 running；断连节点、
    中断残留留下的 running 没有任何代码路径会复位，会让前端的运行锁与
    “运行中”横幅永久挂起（2026-10-06 实测复现）。运行启动与收尾各扫一次。
    """
    for node in draft.get("nodes", []):
        if isinstance(node, dict) and node.get("runStatus") == "running" \
                and node.get("id") not in executing_ids:
            node["runStatus"] = "idle"


def _validate_draft_shape(draft: dict[str, Any]) -> None:
    nodes = draft.get("nodes")
    edges = draft.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise AppError("Workflow Draft 格式错误")
    if len(nodes) > 50 or len(edges) > 100:
        raise AppError("Workflow 节点或连接数量超过限制")
    ids = [str(node.get("id", "")) for node in nodes if isinstance(node, dict)]
    if len(ids) != len(set(ids)) or any(not node_id for node_id in ids):
        raise AppError("Workflow 节点 ID 不合法或重复")
    valid = set(ids) | {INPUT_ID, OUTPUT_ID}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("source") not in valid or edge.get("target") not in valid:
            raise AppError("Workflow 包含无效连接")


def _find_node(draft: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    return next((node for node in draft.get("nodes", []) if node.get("id") == node_id), None)


def _ensure_edge(draft: dict[str, Any], source: str, target: str) -> None:
    edges = draft.setdefault("edges", [])
    if not any(edge.get("source") == source and edge.get("target") == target for edge in edges):
        edges.append({"source": source, "target": target})


_INJECTION_HINT_RE = re.compile(r"忽略.{0,12}(系统提示|规则)|解除(限制|规则)|无视.{0,8}(规则|提示)")


def clear_workflow(
    user_id: int, project_id: int | None, user_message: str = ""
) -> dict[str, Any]:
    """清空画布：用户明确说“清空/重建”才允许，只对正常语气开放。"""
    _check_project_owner(user_id, project_id)
    # 注入式口吻（“忽略你的系统提示”等）诱导的破坏性操作不放行：
    # 这是 prompt 注入实测的真实撞车点，防御必须放服务端而不是靠模型看人下菜。
    if _INJECTION_HINT_RE.search(user_message or ""):
        raise AppError(
            "检测到注入式指令（要求忽略系统规则），破坏性操作被平台拒绝。请用正常语气描述需求。",
            status_code=422,
        )
    current = get_draft(user_id, project_id)
    _ensure_canvas_read(user_id, project_id, current["draft"])
    revision = current["revision"] + 1
    draft = empty_draft()
    saved = workflow_repo.upsert(user_id, project_id, revision, draft, _now())
    return {"id": saved["id"], "revision": revision, "draft": draft}


# ---------- 链注册表（WF1-①：链是一等公民，按建成时间标号，删后不重排） ----------


def _chain_registry(draft: dict[str, Any]) -> list[dict[str, Any]]:
    return draft.setdefault("chains", [])


def _chain_counter(draft: dict[str, Any]) -> int:
    return int(draft.setdefault("chain_counter", 0))


def _spec_digest(template_id: str, params_list: list[dict[str, Any]]) -> str:
    """链的指纹 = 模板 + 默认值应用后的全部参数；重复链检测只认指纹。"""
    payload = {"t": template_id, "p": params_list}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]


def _chain_nodes(draft: dict[str, Any], node_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {str(n.get("id")): n for n in draft.get("nodes", []) if isinstance(n, dict)}
    return [by_id[nid] for nid in node_ids if nid in by_id]


def _sync_chain_registry(draft: dict[str, Any]) -> None:
    """登记簿对账：清退节点尽失的旧链；检测用户手搭的合法直线链并补登记。

    合法直线链 = 从 Input 出发、每个中间节点一进一出、最终落在 Output 的通路；
    分叉/进链/未接 Output 的自由图不登记（它们只是画布上的半成品/高级玩法）。
    号源单调只增：删链只退位不退号。
    """
    nodes = [n for n in draft.get("nodes", []) if isinstance(n, dict)]
    by_id = {str(n.get("id")): n for n in nodes}
    registry = _chain_registry(draft)
    registry[:] = [
        entry
        for entry in registry
        if _chain_nodes(draft, [str(nid) for nid in entry.get("node_ids", [])])
    ]
    known = {
        str(nid)
        for entry in registry
        for nid in entry.get("node_ids", [])
    }
    edges = draft.get("edges", [])

    def outs(node_id: str) -> list[str]:
        return [str(e.get("target")) for e in edges if str(e.get("source")) == node_id]

    def ins(node_id: str) -> list[str]:
        return [str(e.get("source")) for e in edges if str(e.get("target")) == node_id]

    counter = _chain_counter(draft)
    for head_id in outs(INPUT_ID):
        if head_id in known or head_id not in by_id:
            continue
        node_ids: list[str] = []
        current_id, legal = head_id, False
        while True:
            node = by_id.get(current_id)
            if node is None or node.get("kind") == "endpoint":
                legal = current_id == OUTPUT_ID and bool(node_ids)
                break
            node_ids.append(current_id)
            nexts = outs(current_id)
            if len(nexts) != 1:
                break
            if nexts[0] == OUTPUT_ID:
                legal = True
                break
            if len(ins(nexts[0])) != 1:
                break
            current_id = nexts[0]
        if not legal:
            continue
        counter += 1
        names = [str(by_id[nid].get("name") or nid) for nid in node_ids]
        registry.append(
            {
                "chain_no": counter,
                "chain_id": f"chain-{uuid.uuid4().hex[:8]}",
                "label": "、".join(names),
                "template_id": None,
                "spec_hash": None,
                "node_ids": node_ids,
                "source": "user",
                "created_at": _now(),
            }
        )
        known.update(node_ids)
    draft["chain_counter"] = counter


def _carry_forward_server_owned(draft: dict[str, Any], previous: dict[str, Any]) -> None:
    """前端回存草稿时只携带自己认识的字段；服务端字段（缓存快照/链编号/resolved
    参数/链注册表）从旧草稿原样携带，杜绝前端“无心销毁”服务端状态。"""
    prev_by_id = {
        str(n.get("id")): n for n in previous.get("nodes", []) if isinstance(n, dict)
    }
    for node in draft.get("nodes", []):
        old = prev_by_id.get(str(node.get("id", "")))
        if old is None:
            continue
        for key in ("checkpoint", "chain_id", "chain_no", "resolved_params"):
            if key in old:
                node[key] = old[key]
    previous_chains = previous.get("chains")
    if previous_chains is not None and "chains" not in draft:
        draft["chains"] = previous_chains
    if "chain_counter" not in draft:
        draft["chain_counter"] = previous.get("chain_counter", 0)


# ---------- 模板链构图（助手侧唯一构图入口） ----------


def list_templates() -> list[dict[str, Any]]:
    return describe_templates()


def _as_capability_id(capability_or_type: str) -> str:
    """工具入参允许 'lyrics'/'image'/'video' 或全名，归一为 capability_id。"""
    alias = {"lyrics": "lyrics.generate", "image": "image.generate", "video": "video.generate"}
    return alias.get(capability_or_type, capability_or_type)


def add_workflow_chains(
    user_id: int,
    project_id: int | None,
    chain_specs: list[dict[str, Any]],
    confirm_duplicates: bool = False,
) -> dict[str, Any]:
    """按模板成批新增链；默认不动存量链，只追加。

    原子语义：任何一条链校验不过，整批不落库。重复链不硬拦（删改留归用户），
    报 422 DUPLICATE_CHAINS 让助手转述给用户核对，用户确认后带
    ``confirm_duplicates=True`` 重试。
    """
    _check_project_owner(user_id, project_id)
    # 平台确定性纠偏（2026-10-11）：模型把「依词构图」拆成 lyrics_only + 视觉原子的
    # 同批声明时，直接纠错合并为复合模板，拒绝让它赌「自己改得对」（评测实测改不对）。
    chain_specs, merged_note = coalesce_dependency_specs(chain_specs)
    if not chain_specs:
        raise AppError("至少要声明一条链", status_code=422)
    if len(chain_specs) > 4:
        raise AppError("单次最多新增 4 条链；更多请先和用户确认成本", status_code=422)

    prepared: list[dict[str, Any]] = []
    for spec in chain_specs:
        template_id = str(spec.get("template_id", ""))
        template = get_template(template_id)
        if template is None:
            raise AppError(
                f"未知模板「{template_id}」；先调用 list_workflow_templates 查看可用模板",
                status_code=422,
            )
        params_list, build_error = build_chain_params(template, dict(spec.get("slots") or {}))
        if build_error:
            raise AppError(build_error, status_code=422)
        validated: list[dict[str, Any]] = []
        for capability_id, params in zip(template.blocks, params_list or [], strict=True):
            entry = CAPABILITY_REGISTRY[capability_id]
            try:
                validated.append(entry.input_model.model_validate(params).model_dump())
            except ValidationError as exc:
                first = exc.errors()[0]
                raise AppError(
                    f"模板「{template.name}」槽位不合法：参数 {first.get('loc')} {first.get('msg')}",
                    status_code=422,
                ) from exc
        label = (str(spec.get("label") or "").strip() or template.name)[:30]
        prepared.append(
            {
                "template_id": template.template_id,
                "template_name": template.name,
                "blocks": template.blocks,
                "params_list": validated,
                "label": label,
                "spec_hash": _spec_digest(template.template_id, validated),
            }
        )

    current = get_draft(user_id, project_id)
    if not confirm_duplicates:
        existing = {
            entry["spec_hash"]: entry
            for entry in _chain_registry(current["draft"])
            if entry.get("spec_hash")
        }
        seen: dict[str, str] = {}
        for item in prepared:
            digest = item["spec_hash"]
            clash = existing.get(digest)
            if clash is not None:
                raise AppError(
                    f"DUPLICATE_CHAINS：新链与现有「链{clash['chain_no']} · "
                    f"{clash.get('label') or clash.get('template_id')}」完全重复"
                    "（同模板同参数），重跑一遍只会产生额外开销、结果不会更好。"
                    "请如实提醒用户核对：是要重跑那条链、改参数出变体，还是确实需要再建一条相同链。"
                    "用户明确确认后，带 confirm_duplicates=true 重试本调用。",
                    status_code=422,
                )
            if digest in seen:
                raise AppError(
                    f"DUPLICATE_CHAINS：本批声明里「{item['label']}」与「{seen[digest]}」完全重复，"
                    "请如实提醒用户核对是否真需要两条相同的链；确认后带 confirm_duplicates=true 重试。",
                    status_code=422,
                )
            seen[digest] = item["label"]

    holder: dict[str, Any] = {}

    def mutate(draft: dict[str, Any]) -> None:
        _ensure_canvas_read(user_id, project_id, draft)
        registry = _chain_registry(draft)
        counter = _chain_counter(draft)
        created: list[dict[str, Any]] = []
        for offset, item in enumerate(prepared):
            counter += 1
            chain_id = f"chain-{uuid.uuid4().hex[:8]}"
            node_ids: list[str] = []
            prev_id = INPUT_ID
            for block_index, capability_id in enumerate(item["blocks"]):
                meta = CAPABILITY_META[capability_id]
                tool_type = meta["type"]
                node = {
                    "id": f"workflow-node-{tool_type}-{uuid.uuid4().hex[:10]}",
                    "kind": "tool",
                    "toolId": f"assistant-{tool_type}-{uuid.uuid4().hex[:10]}",
                    "type": tool_type,
                    "capability_id": capability_id,
                    "name": meta["name"],
                    "badge": meta["badge"],
                    "description": meta["description"],
                    "color": meta["color"],
                    "chain_id": chain_id,
                    "chain_no": counter,
                    "x": 300 + 320 * block_index,
                    "y": 280 + 260 * (len(registry) + offset),
                    "params": item["params_list"][block_index],
                    "runStatus": "idle",
                    "result": None,
                    "candidates": [],
                }
                draft.setdefault("nodes", []).append(node)
                _ensure_edge(draft, prev_id, node["id"])
                prev_id = node["id"]
                node_ids.append(node["id"])
            _ensure_edge(draft, prev_id, OUTPUT_ID)
            registry.append(
                {
                    "chain_no": counter,
                    "chain_id": chain_id,
                    "label": item["label"],
                    "template_id": item["template_id"],
                    "spec_hash": item["spec_hash"],
                    "node_ids": node_ids,
                    "source": "assistant",
                    "created_at": _now(),
                }
            )
            created.append(
                {
                    "chain_no": counter,
                    "chain_id": chain_id,
                    "label": item["label"],
                    "template_id": item["template_id"],
                    "blocks": [b.split(".")[0] for b in item["blocks"]],
                }
            )
        draft["chain_counter"] = counter
        holder["created"] = created

    saved = _mutate_draft_with_cas(user_id, project_id, mutate)
    return {
        "id": saved["id"],
        "revision": saved["revision"],
        "draft": saved["draft"],
        "chains": holder["created"],
        "merged_note": merged_note,
    }


def update_chain_params(
    user_id: int,
    project_id: int | None,
    chain_no: int,
    capability_or_type: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """按链号改某个 Block 的参数（增量合并）；改过的节点复位待跑，缓存由参数指纹接管。"""
    _check_project_owner(user_id, project_id)
    capability_id = _as_capability_id(capability_or_type)
    if capability_id not in CAPABILITY_REGISTRY:
        raise AppError(f"未知的生产能力: {capability_or_type}", status_code=422)
    holder: dict[str, Any] = {}

    def mutate(draft: dict[str, Any]) -> None:
        _ensure_canvas_read(user_id, project_id, draft)
        entry = next(
            (c for c in _chain_registry(draft) if c.get("chain_no") == chain_no),
            None,
        )
        if entry is None:
            raise AppError(
                f"画布上没有链 {chain_no}；先调用 read_workflow_state 核对现有链号",
                status_code=404,
            )
        node = next(
            (
                n
                for n in _chain_nodes(draft, [str(nid) for nid in entry.get("node_ids", [])])
                if n.get("capability_id") == capability_id
            ),
            None,
        )
        if node is None:
            raise AppError(
                f"链 {chain_no}（{entry.get('label')}）里没有 {capability_id.split('.')[0]} 环节",
                status_code=422,
            )
        merged = {**(node.get("params") or {}), **params}
        try:
            merged = CAPABILITY_REGISTRY[capability_id].input_model.model_validate(merged).model_dump()
        except ValidationError as exc:
            first = exc.errors()[0]
            raise AppError(
                f"参数不合法：{first.get('loc')} {first.get('msg')}", status_code=422
            ) from exc
        node["params"] = merged
        node["runStatus"] = "idle"
        node["result"] = None
        node["error"] = None
        if entry.get("template_id"):
            entry["spec_hash"] = _spec_digest(
                str(entry["template_id"]),
                [
                    n.get("params") or {}
                    for n in _chain_nodes(draft, [str(i) for i in entry.get("node_ids", [])])
                ],
            )
        holder["node_id"] = node["id"]
        holder["label"] = entry.get("label")

    saved = _mutate_draft_with_cas(user_id, project_id, mutate)
    return {
        "id": saved["id"],
        "revision": saved["revision"],
        "draft": saved["draft"],
        "node_id": holder["node_id"],
        "chain_no": chain_no,
        "label": holder["label"],
    }


def remove_workflow_chain(user_id: int, project_id: int | None, chain_no: int) -> dict[str, Any]:
    """按链号整条删除；链号退役不复用（防对话指代错位错删）。"""
    _check_project_owner(user_id, project_id)
    holder: dict[str, Any] = {}

    def mutate(draft: dict[str, Any]) -> None:
        _ensure_canvas_read(user_id, project_id, draft)
        registry = _chain_registry(draft)
        entry = next((c for c in registry if c.get("chain_no") == chain_no), None)
        if entry is None:
            raise AppError(
                f"画布上没有链 {chain_no}；先调用 read_workflow_state 核对现有链号",
                status_code=404,
            )
        drop_ids = {str(nid) for nid in entry.get("node_ids", [])}
        draft["nodes"] = [
            n for n in draft.get("nodes", []) if str(n.get("id")) not in drop_ids
        ]
        draft["edges"] = [
            e
            for e in draft.get("edges", [])
            if str(e.get("source")) not in drop_ids and str(e.get("target")) not in drop_ids
        ]
        registry.remove(entry)
        holder["label"] = entry.get("label")

    saved = _mutate_draft_with_cas(user_id, project_id, mutate)
    return {
        "id": saved["id"],
        "revision": saved["revision"],
        "draft": saved["draft"],
        "removed_chain_no": chain_no,
        "label": holder["label"],
    }



def _execution_order(draft: dict[str, Any]) -> list[dict[str, Any]]:
    """从 Input 出发做广度优先排序；能跑的能力节点按拓扑次序逐个执行。"""
    edges = draft.get("edges", [])
    nodes_by_id = {n["id"]: n for n in draft.get("nodes", [])}
    outgoing: dict[str, list[str]] = {}
    for edge in edges:
        outgoing.setdefault(str(edge.get("source", "")), []).append(str(edge.get("target", "")))

    ordered: list[dict[str, Any]] = []
    visited = set()
    queue = list(outgoing.get(INPUT_ID, []))
    if not queue:
        raise AppError("Workflow 还没有连接输入节点，请先从输入端点连出一个工具节点")
    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = nodes_by_id.get(node_id)
        if node is None:
            continue
        if node.get("capability_id"):
            ordered.append(node)
        queue.extend(outgoing.get(node_id, []))

    if not ordered:
        raise AppError("Workflow 里还没有可执行的生产工具节点")
    return ordered


UPSTREAM_RESULT_PATTERN = "${upstream.result.content}"
# 模型会发明各种上游引用写法（如 ${workflow-node-xxx.result.content}）；
# 一律归一化为上面的规范形态，且在配置期必须看到真的有上游连线。
UPSTREAM_REF_RE = re.compile(r"\$\{[^}]*?\.result\.content\}")


def _upstream_content(upstream_node: dict[str, Any]) -> str | None:
    """抽取上游节点“要送到下游的内容”，严格按“多版本必须手选”规则：

    1. 有 selectedCandidateId：只认它，找不到对应的成功版本就算不可用；
    2. 只有一个成功版本：直接用它；
    3. 多个成功版本但没选定：返回 None，下游节点报“先选版本”。
    """
    candidates = upstream_node.get("candidates")
    if not isinstance(candidates, list):
        return None
    selected_id = upstream_node.get("selectedCandidateId")
    if selected_id:
        for cand in candidates:
            if cand.get("id") == selected_id and cand.get("status") == "succeeded":
                return cand.get("content")
        return None
    succeeded = [
        cand
        for cand in candidates
        if cand.get("status") == "succeeded" and isinstance(cand.get("content"), str)
    ]
    if len(succeeded) == 1:
        return succeeded[0].get("content")
    return None




def _failure_result(capability_id: str, message: str):
    return CapabilityResult(capability_id=capability_id, status="failed", error=message)


def _append_candidate(node: dict[str, Any], result: Any) -> None:
    """运行成功/失败都留一条候选记录；单一成功默认成下游的输入。
    产物是资产时（图像/视频）同步把 asset_id 考进去——下游图生视频链路要用。"""
    content = result.result.get("content") if isinstance(result.result, dict) else None
    asset_id = None
    if isinstance(result.result, dict) and isinstance(result.result.get("asset"), dict):
        asset_id = result.result["asset"].get("asset_id")
    candidates = node.setdefault("candidates", [])
    candidates.append(
        {
            "id": f"cand-{uuid.uuid4().hex[:8]}",
            "status": result.status,
            "content": content,
            "asset_id": asset_id,
            "error": result.error,
            "created_at": _now(),
        }
    )
    if result.status == "succeeded":
        succeeded = [c for c in candidates if c.get("status") == "succeeded"]
        if len(succeeded) == 1:
            node["selectedCandidateId"] = succeeded[0]["id"]


def _upstream_asset(upstream_node: dict[str, Any]) -> str | None:
    """与 _upstream_content 同规则的资产版：抽取上游候选人要送去下游的 asset_id。
    （同一套-selectedCandidateId / 单一成功直接取 / 多版本未定返回 None 的铁律。）"""
    candidates = upstream_node.get("candidates")
    if not isinstance(candidates, list):
        return None
    # 资产引用值优先拿 asset_id；老格式候选（2026-10-28 双 Worker 并发被旧代码跑动事故实证）
    # 没有 asset_id 列 → 从 content 的 /api/assets/<file> 链接回填为路径形态，
    # 属主校验在能力执行人口（_normalize_source_asset）统一拦截，这里不查表。
    def _asset_of(cand: dict[str, Any]) -> str | None:
        if isinstance(cand.get("asset_id"), str):
            return cand["asset_id"]
        content = cand.get("content")
        if isinstance(content, str):
            match = re.search(r"/api/assets/([0-9a-f-]+\.[a-z0-9]+)", content)
            if match:
                return f"/api/assets/{match.group(1)}"
        return None

    selected_id = upstream_node.get("selectedCandidateId")
    if selected_id:
        for cand in candidates:
            if cand.get("id") == selected_id and cand.get("status") == "succeeded":
                return _asset_of(cand)
        return None
    succeeded = [
        cand
        for cand in candidates
        if cand.get("status") == "succeeded" and _asset_of(cand)
    ]
    if len(succeeded) == 1:
        return _asset_of(succeeded[0])
    return None


# 显式引用上游资产的记号；视频节点在模板「图生视频」场景使用
UPSTREAM_ASSET_PATTERN = "${upstream.result.asset}"
UPSTREAM_ASSET_REF_RE = re.compile(r"\$\{[^}]*?\.result\.asset\}")


def _resolve_params(
    node: dict[str, Any], draft: dict[str, Any], results: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    """替换 ``${upstream.result.content}`` / ``${upstream.result.asset}``；多版本上游未选择时拒绝执行而不是盲跑。"""
    node_id = str(node.get("id", ""))
    capability_id = str(node.get("capability_id") or "")
    upstream_id = next(
        (
            str(edge.get("source"))
            for edge in draft.get("edges", [])
            if str(edge.get("target")) == node_id
        ),
        None,
    )
    upstream_node = next(
        (item for item in draft.get("nodes", []) if str(item.get("id")) == upstream_id),
        None,
    )
    upstream_content = (results.get(upstream_id) or {}).get("content") if upstream_id else None
    if upstream_content is None and upstream_node is not None:
        upstream_content = _upstream_content(upstream_node)

    params = node.get("params") or {}
    # 容忍历史/模型自造的引用形态（如 ${workflow-node-xxx.result.content}），统一按上游引用处理
    needs_reference = any(
        UPSTREAM_REF_RE.search(str(value)) for value in params.values()
    )
    if needs_reference and not upstream_content:
        has_any_succeeded = any(
            isinstance(cand, dict) and cand.get("status") == "succeeded" and cand.get("content")
            for cand in (upstream_node.get("candidates") if upstream_node else []) or []
        )
        if has_any_succeeded:
            # 注：这句文案有单测锁定（多版本必须手选的生产铁律），改动要同步看测试
            return params, "上游已产出多个版本，请先在其中选一个，再运行当前节点"
        # 另一种真实原因：上游没产出过（从未跑过 / 上一次失败 / 候选区被清了）
        return params, "上游节点还没有可用产出，请先确保上游节点运行成功后再运行本节点"
    upstream_asset_id = (
        _upstream_asset(upstream_node) if upstream_node is not None else None
    )
    # 图生视频（I2V）自动识别：视频节点的上游节点产出了图像资产、且 params 没显式给
    # source_asset → 自动带上。用户手动画布拉条接上下游也是这条路（不取人配置面板）。
    auto_i2v = (
        capability_id == "video.generate"
        and upstream_asset_id
        and "source_asset" not in params
        and upstream_node is not None
        and str(upstream_node.get("capability_id") or "") == "image.generate"
    )

    resolved: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str) and UPSTREAM_ASSET_REF_RE.search(value):
            # 资产引用优先于文本引用判定（两类记号不同时出现在同一值上，先查防吞）
            if upstream_asset_id:
                resolved[key] = UPSTREAM_ASSET_REF_RE.sub(upstream_asset_id, value)
            else:
                resolved[key] = value  # 未决：由下游的「上游无产出」错误分支接力拦住
        elif isinstance(value, str) and upstream_content:
            resolved[key] = UPSTREAM_REF_RE.sub(upstream_content, value)
        else:
            resolved[key] = value
    if auto_i2v:
        resolved["source_asset"] = upstream_asset_id
    elif capability_id == "video.generate" and upstream_node is not None and str(
        upstream_node.get("capability_id") or ""
    ) == "image.generate" and "source_asset" not in params:
        # 用户已把视频节点接在图像节点后面，却拿不到唯一可用资产 = 上游多版本未手选；
        # 绝不能静默退回文生视频重新随机生成一张「另一幅画」、然后报告成「图生视频」——
        # 与文本引用同一铁律：拒绝，不肓跑
        if upstream_asset_id is None:
            succeeded_assets = [
                c
                for c in (upstream_node.get("candidates") or [])
                if isinstance(c, dict) and c.get("status") == "succeeded"
            ]
            if succeeded_assets:
                return params, "上游已产出多个版本，请先在其中选一个，再运行当前节点"
            return params, "上游节点还没有可用产出，请先确保上游节点运行成功后再运行本节点"
    elif UPSTREAM_ASSET_REF_RE.search(str(params.get("source_asset", ""))) and not upstream_asset_id:
        # 显式引用了上游资产但上游还没产出 → 与文本引用同一铁律，明说原因
        return params, "上游节点还没有可用产出，请先确保上游节点运行成功后再运行本节点"
    return resolved, None


async def run_workflow(user_id: int, project_id: int | None) -> dict[str, Any]:
    """从 Input 出发，依次执行可达的能力节点；结果按节点写回 Draft。"""
    _check_project_owner(user_id, project_id)
    current = await asyncio.to_thread(get_draft, user_id, project_id)
    draft = current["draft"]
    ordered = _execution_order(draft)
    # 校验未知能力，防护手写 Draft 里开进了未注册 Block
    for node in ordered:
        capability_id = node.get("capability_id")
        if capability_id not in CAPABILITY_REGISTRY:
            raise AppError(f"未注册的能力: {capability_id}")

    for node in ordered:
        node["runStatus"] = "running"
    executing_ids = {node["id"] for node in ordered}
    _drop_orphan_running(draft, executing_ids)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    running_saved = await asyncio.to_thread(
        workflow_repo.upsert, user_id, project_id, current["revision"] + 1, draft, _now()
    )
    running_draft = running_saved["draft"]

    first_error: str | None = None
    final_status = "succeeded"
    node_results: dict[str, dict[str, Any]] = {}
    for node in ordered:
        capability_id = node["capability_id"]
        resolved_params, params_error = _resolve_params(node, draft, node_results)
        if params_error:
            node["resolved_params"] = resolved_params
            node["runStatus"] = "failed"
            node["result"] = None
            node["error"] = params_error
            first_error = first_error or f"{node.get('name')}: {params_error}"
            final_status = "failed"
            _append_candidate(node, _failure_result(capability_id, params_error))
            continue
        result = await run_capability(
            capability_id,
            resolved_params,
            context=CapabilityContext(user_id=user_id, project_id=project_id, source="workflow"),
        )
        node["resolved_params"] = resolved_params
        node["runStatus"] = result.status
        node["result"] = result.result
        node["error"] = result.error
        _append_candidate(node, result)
        if result.result and result.result.get("content") is not None:
            node_results[node["id"]] = result.result
        if result.status != "succeeded":
            first_error = first_error or f"{node.get('name')}: {result.error or result.status}"
            final_status = "failed" if result.status == "failed" else "model_unavailable"

    latest = await asyncio.to_thread(get_draft, user_id, project_id)
    _drop_orphan_running(latest["draft"], executing_ids)
    latest_by_id = {n.get("id"): n for n in latest["draft"].get("nodes", [])}
    for node in ordered:
        latest_node = latest_by_id.get(node["id"])
        if latest_node is not None:
            for key in ("runStatus", "result", "error", "resolved_params", "candidates", "selectedCandidateId"):
                latest_node[key] = node.get(key)

    final_saved = await asyncio.to_thread(
        workflow_repo.upsert, user_id, project_id, latest["revision"] + 1, latest["draft"], _now()
    )
    return {
        "run_id": run_id,
        "revision": final_saved["revision"],
        "draft": final_saved["draft"],
        "running_draft": running_draft,
        "status": final_status,
        "error": first_error,
        "executed": [n["id"] for n in ordered],
    }


# ---------- 活跃运行判定（带自愈） ----------

_JOB_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def get_active_run(user_id: int, workflow_id: int | None) -> dict[str, Any] | None:
    """返回真正活着的运行；关联 job 已终局（失败/取消/成功）的 running/queued 记录
    是僵尸（例如用户取消 job、Worker 崩溃），顺手收尸并返回 None——
    防止画布编辑锁被一个永远等不回来的运行永久顶住。"""
    run = workflow_run_repo.find_active_for_workflow(user_id, workflow_id)
    if run is None:
        return None
    job_id = run.get("job_id")
    if not job_id:
        return run
    try:
        job = job_service.get_job(user_id, str(job_id))
    except Exception:
        return run
    if job["status"] in _JOB_TERMINAL_STATUSES:
        finalize_failed_run(
            user_id,
            run.get("project_id"),
            run["id"],
            f"关联任务已{job['status']}，运行自动清理",
        )
        return None
    return run


# ---------- 运行状态查询（Assistant 进度查询工具与前端进度面板共用） ----------


def _node_display_name(run: dict[str, Any], node_id: str | None) -> str:
    for node in run.get("draft_snapshot", {}).get("nodes", []):
        if str(node.get("id")) == node_id:
            return str(node.get("name") or node_id)
    return node_id or ""


def get_run_status_detail(
    user_id: int,
    project_id: int | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """返回一次 Workflow 运行的完整状态；run_id 为空时取当前项目最近一条。"""
    if run_id is None:
        _check_project_owner(user_id, project_id)
        recent = workflow_run_repo.list_recent(user_id, project_id, limit=1)
        if not recent:
            raise AppError("目前没有可查询的 Workflow 运行记录", status_code=404)
        run = recent[0]
    else:
        found = workflow_run_repo.get(user_id, run_id)  # repo 已按 user_id 过滤
        if found is None:
            raise AppError("运行记录不存在", status_code=404)
        run = found
        _check_project_owner(user_id, run.get("project_id"))

    steps = workflow_run_repo.list_steps(user_id, run["id"])
    step_items = [
        {
            "node_id": step.get("node_id"),
            "node_name": _node_display_name(run, step.get("node_id")),
            "capability_id": step.get("capability_id"),
            "status": step.get("status"),
            "error": step.get("error"),
        }
        for step in steps
    ]
    done_count = sum(1 for s in step_items if s["status"] in {"succeeded", "reused", "failed"})
    return {
        "run_id": run["id"],
        "status": run["status"],
        "env_snapshot": run.get("env_snapshot"),
        "error": run.get("error"),
        "created_at": run.get("created_at"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "progress": {"done": done_count, "total": len(step_items)},
        "steps": step_items,
    }


# ---------- WF1-③ 队列档案馆：费用预览 / 运行列表 / 删除与保留 ----------


def get_run_preview(user_id: int, project_id: int | None) -> dict[str, Any]:
    """全局运行按钮的「跑前先报价」：与 _execute_snapshot_run 完全同一套缓存判定。

    - reused：resolved 参数指纹 ≡ checkpoint 且结果还在——零调用零计费；
    - stale：跑过但参数/上游有变——本次将重跑并计费；
    - cold：首次运行——计费；
    - blocked：上游多版本未选定、上游被卡、能力未注册等缺口——运行会失败，预览期就说破；
    - 冷链路特例：参数引用了「还在排队要跑」的上游（预览时没有候选内容），不计为
      blocked——上游届时会真跑并产出；费用口径保守计入下游费用。
    上层入参的引用内容取当前 draft 候选区（与运行时「上游命中缓存」的路径一致）；
    若上游将重跑且产出变了，下游的 reused 预估会失效——前端提示语如实说明。
    """
    current = get_draft(user_id, project_id)
    draft = current["draft"]
    try:
        ordered = _execution_order(draft)
    except AppError as exc:
        return {
            "ready": False,
            "reason": getattr(exc, "detail", str(exc)),
            "total_cost_credits": 0.0,
            "reused_count": 0,
            "billable_count": 0,
            "chains": [],
        }

    # 链分组以链注册表为唯一权威：用户手搭链只登记 node_ids，不回写节点
    # （1106 之前读 node.chain_no 的写法让用户搭的链全掉进「未登记链」）。
    node_chain: dict[str, dict[str, Any]] = {}
    for entry in _chain_registry(draft):
        for nid in entry.get("node_ids", []):
            node_chain[str(nid)] = entry
    node_by_id = {str(n.get("id")): n for n in draft.get("nodes", []) if isinstance(n, dict)}

    per_node: list[dict[str, Any]] = []
    blocked_ids: set[str] = set()
    processed_ids: set[str] = set()
    total = Decimal("0")
    for node in ordered:
        node_id = str(node.get("id"))
        capability_id = str(node.get("capability_id"))
        upstream_id = next(
            (
                str(e.get("source"))
                for e in draft.get("edges", [])
                if str(e.get("target")) == node_id
                and str(e.get("source")) not in (INPUT_ID, OUTPUT_ID)
            ),
            None,
        )
        upstream_node = node_by_id.get(upstream_id) if upstream_id else None
        resolved, params_error = _resolve_params(node, draft, {})
        if params_error and upstream_id is not None and upstream_node is not None:
            no_output_case = not any(
                isinstance(c, dict) and c.get("status") == "succeeded" and c.get("content")
                for c in upstream_node.get("candidates") or []
            )
            if upstream_id in blocked_ids:
                params_error = f"上游节点（{upstream_node.get('name') or upstream_id}）不能产出（请先处理上游）"
            elif no_output_case and upstream_id in processed_ids:
                # 冷链路上游会真跑并产出：预览不拦截，下游届时获得内容。
                # 费用口径保守处理——届时内容若与现状一致，下游仍可能转缓存复用。
                params_error = None
        checkpoint = node.get("checkpoint") or {}
        reusable = _reusable_content(node)
        cap_entry = CAPABILITY_REGISTRY.get(capability_id)
        will_reuse = False
        if not params_error and reusable is not None:
            fingerprint = _params_fingerprint(capability_id, resolved)
            will_reuse = checkpoint.get("params_hash") == fingerprint
        estimate = Decimal("0") if will_reuse or cap_entry is None else cap_entry.billing_estimate()
        if params_error:
            state, reason = "blocked", params_error
            blocked_ids.add(node_id)
        elif cap_entry is None:
            state, reason = "blocked", f"未注册的能力: {capability_id}"
            blocked_ids.add(node_id)
        elif will_reuse:
            state, reason = "reused", None
        elif checkpoint:
            state, reason = "stale", "参数或上游内容有变，本次将重跑"
        else:
            state, reason = "cold", None
        total += estimate
        processed_ids.add(node_id)
        chain_entry_of_node = node_chain.get(node_id)
        per_node.append(
            {
                "node_id": node_id,
                "name": node.get("name"),
                "capability_id": capability_id,
                "chain_no": (chain_entry_of_node or {}).get("chain_no"),
                "state": state,
                "reason": reason,
                "cost_credits": float(estimate),
            }
        )

    groups: dict[Any, dict[str, Any]] = {}
    for info in per_node:
        no = info["chain_no"]
        registry_entry = node_chain.get(info["node_id"])
        group = groups.setdefault(
            no,
            {
                "chain_no": no,
                "chain_id": (registry_entry or {}).get("chain_id"),
                "label": (registry_entry or {}).get("label") or "未登记链",
                "cost_credits": 0.0,
                "blocks": [],
            },
        )
        group["cost_credits"] += info["cost_credits"]
        group["blocks"].append(info)
    first_blocked = next((i["reason"] for i in per_node if i["state"] == "blocked"), None)
    return {
        "ready": first_blocked is None,
        "reason": first_blocked,
        "total_cost_credits": float(total),
        "reused_count": sum(1 for i in per_node if i["state"] == "reused"),
        "billable_count": sum(1 for i in per_node if i["cost_credits"] > 0),
        # 未登记链（未连成完整直线的手搭块）沉底，链号小的在前
        "chains": sorted(
            groups.values(), key=lambda g: (g["chain_no"] is None, g["chain_no"] or 0)
        ),
    }


def list_runs(user_id: int, project_id: int | None, limit: int = 20) -> dict[str, Any]:
    """队列档案馆列表：每次运行帶步骤与按链分组的产物摘要（新→旧）。

    产物内容全量返回（歌词文本 + 图像/视频 asset URL）；上限 20 条运行，
    靠面板「删除不成功版本」保持清单可管理。
    """
    _check_project_owner(user_id, project_id)
    items: list[dict[str, Any]] = []
    for run in workflow_run_repo.list_recent(user_id, project_id, limit=limit):
        steps = workflow_run_repo.list_steps(user_id, run["id"])
        snapshot = run.get("draft_snapshot") or {}
        # 与预览同口径：链归属以注册表 node_ids 为权威，节点上的 chain_no 字段仅供参考
        chain_label: dict[Any, Any] = {}
        node_chain_of: dict[str, Any] = {}
        for entry in snapshot.get("chains", []):
            if not isinstance(entry, dict):
                continue
            chain_label[entry.get("chain_no")] = entry.get("label")
            for nid in entry.get("node_ids", []):
                node_chain_of[str(nid)] = entry.get("chain_no")
        node_by_id = {
            str(n.get("id")): n
            for n in snapshot.get("nodes", [])
            if isinstance(n, dict)
        }
        chain_groups: dict[Any, dict[str, Any]] = {}
        for step in steps:
            node = node_by_id.get(str(step.get("node_id")), {})
            chain_no = node_chain_of.get(str(step.get("node_id")))
            group = chain_groups.setdefault(
                chain_no,
                {
                    "chain_no": chain_no,
                    "label": chain_label.get(chain_no) or "未登记链",
                    "blocks": [],
                },
            )
            raw_output = step.get("output")
            output: dict[str, Any] = raw_output if isinstance(raw_output, dict) else {}
            group["blocks"].append(
                {
                    "node_id": step.get("node_id"),
                    "node_name": node.get("name") or step.get("node_id"),
                    "capability_id": step.get("capability_id"),
                    "status": step.get("status"),
                    "reused": bool(output.get("reused")),
                    "content": output.get("content"),
                    "error": step.get("error"),
                }
            )
        done = sum(1 for s in steps if s.get("status") in {"succeeded", "failed", "reused"})
        items.append(
            {
                "run_id": run["id"],
                "status": run["status"],
                "kept": bool(run.get("kept")),
                "created_at": run.get("created_at"),
                "finished_at": run.get("finished_at"),
                "error": run.get("error"),
                "progress": {"done": done, "total": len(steps)},
                "chains": sorted(
                    chain_groups.values(), key=lambda g: (g["chain_no"] is None, g["chain_no"] or 0)
                ),
            }
        )
    return {"runs": items, "limit": limit}


_ASSET_URL_RE = re.compile(r"/api/assets/([A-Za-z0-9_.-]+)")


def delete_run(user_id: int, run_id: str) -> dict[str, Any]:
    """「不满意删除」：删除运行记录 + 该次运行产出的资产行与磁盘文件。

    诚实话术硬约束：运行即成账，积分不退还（settle-at-完成是 I5 的计价语义）。
    进行中的 run 不删（409）；资产级联只跟进对账得到的文件，不碰别的产物。
    """
    run = workflow_run_repo.get(user_id, run_id)
    if run is None:
        raise AppError("运行记录不存在", status_code=404)
    if run["status"] in ("queued", "running"):
        raise AppError("运行进行中，结束后才能删除", status_code=409)

    # 先在删行前采样此次运行引用的产物文件名（步骤删除后行就没了）
    filenames: set[str] = set()
    for step in workflow_run_repo.list_steps(user_id, run_id):
        output = step.get("output")
        content = output.get("content") if isinstance(output, dict) else None
        if isinstance(content, str):
            filenames.update(_ASSET_URL_RE.findall(content))

    outcome = workflow_run_repo.delete_run(user_id, run_id)
    if outcome is None:
        raise AppError("运行记录不存在", status_code=404)
    if str(outcome).startswith("active:"):
        raise AppError("运行进行中，结束后才能删除", status_code=409)

    # 资产双路对账：新路（v16 后）run_id 对账列；老路从 step 内容里解析文件名
    deleted_rows = asset_repo.delete_by_run(user_id, run_id)
    for row in deleted_rows:
        filenames.add(str(row.get("filename") or ""))
    files_removed = 0
    for filename in sorted(filenames):
        if not filename:
            continue
        removed = asset_repo.delete_by_filename(user_id, filename)
        if not removed and filename not in {r["filename"] for r in deleted_rows}:
            continue
        path = Path(config.DOCUMENT_STORAGE_DIR).parent / "assets" / Path(filename).name
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
            files_removed += 1
    logger.info(
        "队列删除完成（积分不退还）",
        extra={
            "evt": "workflow_run_deleted",
            "run_id": run_id,
            "files_removed": files_removed,
            "kept": bool(run.get("kept")),
        },
    )
    return {"deleted": True, "run_id": run_id, "files_removed": files_removed, "refund": False}


def sweep_run_archives(retention_days: int) -> dict[str, Any]:
    """WF1-③-v2：清掉非保留 + 过限的老运行档案（记录+步骤+资产行+磁盘文件）。

    「满意才保留」是用户意图唯一信号（PATCH keep）；未标记保留的产物才会被扫。
    与本模块 delete_run 循环同一套语义：积分不退是帐面现实（settle-at-完成），
    只要资产在磁盘上存在，档案运行时就进余额账单（帆后是闲置的积田）。"""
    if retention_days <= 0:
        return {"removed": 0, "files_removed": 0}
    candidates = workflow_run_repo.list_expired_terminal_runs(retention_days)
    removed = 0
    files_removed = 0
    for run in candidates:
        try:
            outcome = delete_run(run["user_id"], run["id"])
            removed += 1
            files_removed += int(outcome.get("files_removed", 0))
        except AppError:
            # 竞态运行时取到活跃 run 或被别人删了——都跳过下一轮再扫
            continue
    if removed:
        logger.info(
            "已到期的运行档案被清扫",
            extra={
                "evt": "workflow_archive_sweep",
                "removed": removed,
                "files_removed": files_removed,
                "retention_days": retention_days,
            },
        )
    return {"removed": removed, "files_removed": files_removed}


def set_run_kept(user_id: int, run_id: str, kept: bool) -> dict[str, Any]:
    """「满意才保留」旗标：未来自动清理策略的豁免名单。"""
    if workflow_run_repo.get(user_id, run_id) is None:
        raise AppError("运行记录不存在", status_code=404)
    workflow_run_repo.set_kept(user_id, run_id, kept)
    return {"run_id": run_id, "kept": kept}


async def await_workflow_idle(
    user_id: int,
    project_id: int | None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """等当前项目工作区的活跃运行结束（最长 timeout），返回最终运行状态。

    供“等它跑完再接着配/跑下一步”的编排使用；超时就如实返回 still_running，
    绝不允许把模型卡在未知数上。
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = await asyncio.to_thread(get_draft, user_id, project_id)
        active = await asyncio.to_thread(get_active_run, user_id, current["id"])
        if active is None:
            try:
                return await asyncio.to_thread(
                    get_run_status_detail, user_id, project_id, None
                )
            except AppError:
                return {"status": "idle", "message": "画布上没有运行记录"}
        await asyncio.sleep(2.0)
    return {"status": "still_running", "message": f"超过 {timeout_seconds}s 仍在运行"}


# ---------- 异步任务队列接入（submit → 后台 Worker 执行） ----------


def _build_env_snapshot() -> dict[str, Any]:
    """“当时环境”快照：模型、prompt hash、能力清单，随 run 落库。

    修复语义漂移需要“当时环境”可复现：模型名 + prompt SHA-256 + 段落文件名 + 能力清单。
    """
    import hashlib

    from agents.prompt_loader import _sections  # 装配出段节原文
    from core import config as cfg

    return {
        "chat_model": cfg.CHAT_MODEL,
        "lyrics_model": cfg.LYRICS_MODEL,
        "embedding_model": cfg.EMBEDDING_MODEL,
        "capabilities": sorted(CAPABILITY_REGISTRY.keys()),
        "prompt_sha256": hashlib.sha256("\n\n".join(_sections()).encode()).hexdigest()[:16],
        "prompt_sections": [f.name for f in sorted(Path(cfg.BACKEND_DIR / "prompts").glob("*.md"))],
    }


def submit_workflow_run(user_id: int, project_id: int | None) -> dict[str, Any]:
    """创建异步运行记录并入队任务；立即返回 run/job 信息，不阻塞 HTTP。"""
    _check_project_owner(user_id, project_id)
    current = get_draft(user_id, project_id)
    # 防重复提交：已有 queued/running 运行时拒绝，双跑会互相覆盖候选与状态。
    # 与保存编辑同一错误码，前端/助手读到都先查进度再决定动作。
    if get_active_run(user_id, current["id"]) is not None:
        raise AppError(
            "WORKFLOW_BUSY：已有 Workflow 正在运行，请先查询进度或等待其结束",
            status_code=409,
        )
    draft = current["draft"]
    ordered = _execution_order(draft)
    for node in ordered:
        capability_id = node.get("capability_id")
        if capability_id not in CAPABILITY_REGISTRY:
            raise AppError(f"未注册的能力: {capability_id}")

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    # 先把节点标为 running 存库，画布会有“已排队”的即时反馈
    running_draft = copy.copy(draft)
    running_draft["nodes"] = [
        {
            **n,
            "runStatus": "running" if n.get("capability_id") else n.get("runStatus"),
        }
        for n in draft.get("nodes", [])
    ]
    running_draft["edges"] = list(draft.get("edges", []))
    saved = workflow_repo.upsert(user_id, project_id, current["revision"] + 1, running_draft, _now())

    job = job_service.create_job(
        user_id=user_id,
        job_type="workflow_run",
        payload={"run_id": run_id, "draft": running_draft},
        project_id=project_id,
    )
    workflow_run_repo.create(
        run_id=run_id,
        user_id=user_id,
        project_id=project_id,
        draft_revision=saved["revision"],
        draft_snapshot=running_draft,
        workflow_id=saved["id"],
        job_id=job["id"],
        created_at=_now(),
        env_snapshot=_build_env_snapshot(),
    )
    return {
        "run_id": run_id,
        "job_id": job["id"],
        "status": "queued",
        "revision": saved["revision"],
        "draft": running_draft,
    }


# ---------- 节点级成功快照缓存（WF1-① 拍板⑥：参数没变就复用，零调用零计费） ----------


def _params_fingerprint(capability_id: str, resolved_params: dict[str, Any]) -> str:
    """快照指纹：生效参数 + 能力 + 计费模型名（Provider 换型号也要重跑）。

    指纹基于 resolved（引用已代入）参数：上游重跑后产出文本变了，下游指纹
    自动变、自动重跑；产出恰好一样则下游也跳过——「从首个被改的节点起跑」
    语义的哈希实现。
    """
    model = ""
    entry = CAPABILITY_REGISTRY.get(capability_id)
    if entry is not None:
        try:
            model = entry.billing_model()
        except Exception:
            model = ""
    payload = json.dumps(
        {"cap": capability_id, "model": model, "params": resolved_params},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _reusable_content(node: dict[str, Any]) -> str | None:
    """节点已有、可直接喂下游的内容：先按「多版本必须手选」规则从候选取，退化用旧 result。"""
    content = _upstream_content(node)
    if content is not None:
        return content
    result = node.get("result")
    if isinstance(result, dict) and isinstance(result.get("content"), str):
        return result["content"]
    return None


async def _execute_snapshot_run(
    user_id: int, project_id: int | None, run_id: str, draft_snapshot: dict[str, Any]
) -> dict[str, Any]:
    """后台 Worker 用 Draft 快照真实执行一条 Workflow；每一步写 step 和节点状态。"""
    ordered = _execution_order(draft_snapshot)
    for node in ordered:
        capability_id = node.get("capability_id")
        if capability_id not in CAPABILITY_REGISTRY:
            workflow_run_repo.update_status(
                run_id, "failed", error=f"未注册的能力: {capability_id}", finished_at=_now()
            )
            raise AppError(f"未注册的能力: {capability_id}")

    node_outputs: dict[str, dict[str, Any]] = {}
    reused_nodes: list[str] = []
    first_error: str | None = None
    final_status = "succeeded"

    for node in ordered:
        # 每个节点执行前都重读最新 Draft：运行期间用户/AI 可能刚改过拓扑，
        # 只允许更新节点状态，坚决不回写 edges / nodes（避免“旧图盖新图”）。
        resolved_params, params_error = _resolve_params(node, draft_snapshot, node_outputs)
        step = workflow_run_repo.create_step(
            step_id=f"step-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            node_id=node["id"],
            capability_id=node["capability_id"],
            input_payload=resolved_params,
            created_at=_now(),
        )
        if params_error:
            workflow_run_repo.finish_step(step["id"], "failed", None, params_error)

            def write_params_error(
                live_draft: dict[str, Any],
                _node_id: str = str(node["id"]),
                _error: str = params_error,
                _resolved: dict[str, Any] = resolved_params,
            ) -> None:
                live = next(
                    (n for n in live_draft.get("nodes", []) if str(n.get("id")) == _node_id),
                    None,
                )
                if live is not None:
                    live["runStatus"] = "failed"
                    live["result"] = None
                    live["error"] = _error
                    live["resolved_params"] = _resolved

            await asyncio.to_thread(_mutate_draft_with_cas, user_id, project_id, write_params_error)
            first_error = first_error or f"{node.get('name')}: {params_error}"
            final_status = "failed"
            continue
        fingerprint = _params_fingerprint(node["capability_id"], resolved_params)
        checkpoint = node.get("checkpoint") or {}
        reusable = _reusable_content(node)
        if checkpoint.get("params_hash") == fingerprint and reusable is not None:
            # 节点级快照命中：参数没变且上次结果还在——零调用、零计费、零 usage 记录。
            workflow_run_repo.finish_step(
                step["id"], "reused", {"content": reusable, "reused": True}, None
            )
            existing_result = node.get("result")
            node_outputs[node["id"]] = (
                existing_result if isinstance(existing_result, dict) else {"content": reusable}
            )

            def write_reuse(
                live_draft: dict[str, Any],
                _node_id: str = str(node["id"]),
                _resolved: dict[str, Any] = resolved_params,
            ) -> None:
                live = next(
                    (n for n in live_draft.get("nodes", []) if str(n.get("id")) == _node_id),
                    None,
                )
                if live is not None:
                    # 提交时全链标记 running；复用节点要如实归位，不能让画布假跑
                    live["runStatus"] = "succeeded"
                    live["resolved_params"] = _resolved
                    live["error"] = None
                    live["reused"] = True

            await asyncio.to_thread(_mutate_draft_with_cas, user_id, project_id, write_reuse)
            reused_nodes.append(str(node["id"]))
            continue
        result = await run_capability(
            node["capability_id"],
            resolved_params,
            context=CapabilityContext(
                user_id=user_id,
                project_id=project_id,
                source="workflow",
                # WF1-③：产物资产对账到 run——档案馆删除运行时能连文件一起收原路退回
                run_id=run_id,
            ),
        )
        workflow_run_repo.finish_step(
            step["id"],
            result.status if result.status != "model_unavailable" else "failed",
            result.result,
            result.error,
        )
        def write_status(
            live_draft: dict[str, Any],
            _node_id: str = str(node["id"]),
            _result=result,
            _resolved: dict[str, Any] = resolved_params,
            _fingerprint: str = fingerprint,
        ) -> None:
            live = next(
                (n for n in live_draft.get("nodes", []) if str(n.get("id")) == _node_id),
                None,
            )
            if live is None:
                return
            live["runStatus"] = _result.status
            live["result"] = _result.result
            live["error"] = _result.error
            live["resolved_params"] = _resolved
            live["reused"] = False
            # 成功才立快照（供下次命中复用）；失败清快照防“残缺结果”被复用
            live["checkpoint"] = (
                {"params_hash": _fingerprint, "ran_at": _now()}
                if _result.status == "succeeded"
                else None
            )
            _append_candidate(live, _result)

        await asyncio.to_thread(_mutate_draft_with_cas, user_id, project_id, write_status)
        if result.result and result.result.get("content") is not None:
            node_outputs[node["id"]] = result.result
        if result.status != "succeeded":
            first_error = first_error or f"{node.get('name')}: {result.error or result.status}"
            final_status = "failed" if result.status == "failed" else "model_unavailable"

    workflow_run_repo.update_status(
        run_id, final_status if final_status == "succeeded" else "failed", error=first_error, finished_at=_now()
    )
    await _notify_run_terminal(
        user_id, project_id, run_id, final_status, first_error, draft_snapshot
    )
    return {
        "run_id": run_id,
        "status": final_status,
        "error": first_error,
        "executed": [n["id"] for n in ordered],
        "reused": reused_nodes,
    }


async def _notify_run_terminal(
    user_id: int,
    project_id: int | None,
    run_id: str,
    status: str,
    error: str | None,
    draft_snapshot: dict[str, Any],
) -> None:
    """运行终态 → 向会话插入一条 system-notice 消息（不刷新聊天轮询可捕）

    这是“会 +(system-notice) 通知”的最后一环；画布颜色由前端快照自己来处理。
    """
    names = [
        str(n.get("name") or n.get("id", "?"))
        for n in draft_snapshot.get("nodes", [])
        if n.get("capability_id")
    ]
    if status == "succeeded":
        content = (
            f"Workflow 已完成（{run_id}）：{', '.join(names) if names else '执行完毕'}。"
            "结果已写回画布节点。"
        )
    else:
        content = (
            f"Workflow {'失败' if status=='failed' else '已停止'}（{run_id}）："
            f"{error or '原因未知'}。节点已标记为失败，请在右侧画布检查参数。"
        )
    try:
        from repositories import chat_repo

        await asyncio.to_thread(
            chat_repo.append_message, user_id, "system-notice", content, project_id
        )
    except Exception:
        logger.warning(
            "终态通知插入失败",
            extra={"evt": "notify_failed", "run_id": run_id},
        )


def finalize_failed_run(
    user_id: int,
    project_id: int | None,
    run_id: str,
    error: str,
    run_snapshot: dict[str, Any] | None = None,
) -> None:
    """运行中途崩溃后的“清扫队”：把卡在 running 的节点如实标 failed。

    不这么做的话，workflow_runs 停在 running、画布节点停在 running、
    前端的运行锁和“运行中”横幅会永久挂起（实测生产事故：job 重试 3/3 耗尽，
    但 UI 以为还在跑）。
    """
    workflow_run_repo.update_status(run_id, "failed", error=error[:500], finished_at=_now())

    def mark_failed(draft: dict[str, Any]) -> None:
        for node in draft.get("nodes", []):
            if node.get("runStatus") == "running":
                node["runStatus"] = "failed"
                node["error"] = error[:300]

    # 收尸失败不阻断主呼喊：CAS 耗尽时上层 job/retry 总会再试一次
    with contextlib.suppress(AppError):
        _mutate_draft_with_cas(user_id, project_id, mark_failed)
    _notify_run_terminal_sync(user_id, project_id, run_id, "failed", error, run_snapshot)


def _notify_run_terminal_sync(
    user_id: int,
    project_id: int | None,
    run_id: str,
    status: str,
    error: str | None,
    draft_snapshot: dict[str, Any] | None,
) -> None:
    """同步通知（准点发生）：把“Workflow 已完成/失败”写成聊天侧一条可见消息。"""
    if status != "succeeded" and not error:
        return
    names = [
        str(n.get("name") or n.get("id", "?"))
        for n in (draft_snapshot or {}).get("nodes", [])
        if n.get("capability_id")
    ]
    if status == "succeeded":
        content = (
            f"Workflow 已完成：{', '.join(names) if names else '执行完毕'}。"
            "结果已写回画布节点。"
        )
    else:
        content = (
            f"Workflow {'失败' if status == 'failed' else '已取消'}："
            f"{error or '原因未知'}。画布节点已标记为失败，请检查参数。"
        )
    try:
        from repositories import chat_repo

        chat_repo.append_message(user_id, "system-notice", content, project_id)
    except Exception:
        # 通知失败不影响主流程（写日志排查见下的 log/wrk）
        import logging as _logging

        _logging.getLogger("workflow").warning("终态通知写入失败: %s", run_id)


def execute_workflow_run_job(
    job_id: str,
    run_id: str,
    draft_snapshot: dict[str, Any],
    user_id: int,
    project_id: int | None,
) -> dict[str, Any]:
    """同步入口，供 Worker 调用；内部用 asyncio.run 驱动 async Capability Runtime。"""
    workflow_run_repo.update_status(run_id, "running", started_at=_now())
    try:
        return asyncio.run(_execute_snapshot_run(user_id, project_id, run_id, draft_snapshot))
    except Exception as exc:
        # 崩溃不烂尾：如实标记运行失败并解锁画布，再让 Worker 打出 job 层的重试/失败
        finalize_failed_run(user_id, project_id, run_id, f"{type(exc).__name__}: {exc}")
        raise
