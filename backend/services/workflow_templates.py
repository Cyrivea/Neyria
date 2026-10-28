"""Workflow 模板注册表：助手构图的唯一合法素材（WF1-①）。

模板只是一条「直线链」的配方：Block 序列 + 槽位表 + 依赖注入规则。
连线永远由平台机械完成（各链接到共享的 Input/Output 端点），模型与模板
都不接触 edges，因此多链并存也不存在错接/乱接的可能。

依赖注入约定（2026-10-06 拍板，2026-10-28 增补 I2V）：
- 依词生图（lyrics_cover / full_mv 的图）：图像 prompt = 槽位画面描述 + 歌词引用；
- 歌词→视频（lyrics_video）：视频 prompt = 槽位镜头描述 + 歌词引用；
- 图生视频（image_anim / cover_mv）：视频节点接 source_asset 槽位/上游图像引用
  （``${upstream.result.asset}``），画面是那张图动起来而不是重新生成另一幅；
- full_mv 的视频不接图引用：Wan2.2 是纯文生视频，把图像产物的 Markdown
  链接拼进文本 prompt 只会污染镜头描述；MV 意境由助手在 mv_prompt 槽位里写足。
"""

from __future__ import annotations

from typing import Any

UPSTREAM_REF = "${upstream.result.content}"
UPSTREAM_ASSET_REF = "${upstream.result.asset}"

_I2V_SLOTS: dict[str, SlotDef] = {
    "anim_prompt": ("video.generate", "prompt", None, True, "镜头运动描述（图上怎么动）"),
    "anim_style": ("video.generate", "style", "电影写实", False, "视觉风格"),
    "anim_duration": ("video.generate", "duration", "5 秒", False, "视频时长"),
    # 来源图片：助手从对话里见到的 /api/assets/xxx.jpg 或资产 id 都接受（运行时归一）
    "source_asset": ("video.generate", "source_asset", None, True, "来源图片（资产 id 或 /api/assets/ 路径）"),
}

# 槽位目录：槽位名 → (能力, 参数名, 默认值, 是否必填, 中文标签)
# 默认值与 schemas/capability.py 的 pydantic 默认对齐；枚举合法性由 pydantic 模型把关
# （辅助模型只填字符串，校验交给 input_model，单一事实源不出第二份枚举表）。
SlotDef = tuple[str, str, str | None, bool, str]
#                能力             参数名          默认值      必填   标签

_LYRICS_SLOTS: list[tuple[str, SlotDef]] = [
    ("theme", ("lyrics.generate", "theme", None, True, "歌词主题")),
    ("style", ("lyrics.generate", "style", "流行抒情", False, "歌曲风格")),
    ("mood", ("lyrics.generate", "mood", "温柔、克制", False, "歌词情绪")),
    ("language", ("lyrics.generate", "language", "中文", False, "歌词语言")),
]
_IMAGE_SLOTS: list[tuple[str, SlotDef]] = [
    ("prompt", ("image.generate", "prompt", None, True, "画面描述")),
    ("style", ("image.generate", "style", "电影概念艺术", False, "视觉风格")),
    ("ratio", ("image.generate", "ratio", "16:9", False, "画面比例")),
    ("palette", ("image.generate", "palette", "深蓝与紫色", False, "色彩方向")),
]
_VIDEO_SLOTS: list[tuple[str, SlotDef]] = [
    ("prompt", ("video.generate", "prompt", None, True, "镜头与画面描述")),
    ("style", ("video.generate", "style", "电影写实", False, "视觉风格")),
    ("ratio", ("video.generate", "ratio", "16:9", False, "画面比例")),
    ("duration", ("video.generate", "duration", "5 秒", False, "视频时长")),
]
# 复合模板里同名能力出现时的槽位改名（cover_*/mv_*/lyrics_* 前缀）
_COVER_SLOTS: list[tuple[str, SlotDef]] = [
    (f"cover_{name}", spec) for name, spec in _IMAGE_SLOTS
]
_MV_SLOTS: list[tuple[str, SlotDef]] = [
    (f"mv_{name}", spec) for name, spec in _VIDEO_SLOTS
]
_LYRICS_PREFIXED: list[tuple[str, SlotDef]] = [
    (f"lyrics_{name}", spec) for name, spec in _LYRICS_SLOTS[1:]
]


class TemplateSpec:
    def __init__(
        self,
        template_id: str,
        name: str,
        description: str,
        blocks: list[str],
        slots: list[tuple[str, SlotDef]],
        inject: dict[str, str] | None = None,
        asset_ref: dict[str, str] | None = None,
    ) -> None:
        self.template_id = template_id
        self.name = name
        self.description = description
        self.blocks = blocks
        self.slots = slots
        # param 注入规则：capability_id → 依赖说明文案（出现在 prompt 的引用前导句）
        self.inject = inject or {}
        # 资产依赖规则：capability_id → 参数名，运行时用上游资产引用记号预填
        # （cover_mv 的视频节点要 source_asset = ${upstream.result.asset}）
        self.asset_ref = asset_ref or {}


WORKFLOW_TEMPLATES: dict[str, TemplateSpec] = {
    "lyrics_only": TemplateSpec(
        "lyrics_only",
        "写一首歌",
        "只做歌词：主题 → 歌词成稿。",
        ["lyrics.generate"],
        _LYRICS_SLOTS,
    ),
    "image_only": TemplateSpec(
        "image_only",
        "画一张图",
        "只做图像：描述 → 图片（与歌曲无关时使用，如用户只要一张封面/海报）。",
        ["image.generate"],
        _IMAGE_SLOTS,
    ),
    "video_only": TemplateSpec(
        "video_only",
        "做一段视频",
        "只做视频：镜头描述 → 短视频（与歌曲无关时使用）。",
        ["video.generate"],
        _VIDEO_SLOTS,
    ),
    "lyrics_cover": TemplateSpec(
        "lyrics_cover",
        "一首歌 + 依词封面",
        "歌词 → 依词生封面：封面画面由平台自动引用歌词结果，模型只需写画面槽位。",
        ["lyrics.generate", "image.generate"],
        [*_LYRICS_SLOTS, *_COVER_SLOTS],
        inject={"image.generate": "请围绕以下歌词的意境创作视觉画面："},
    ),
    "lyrics_video": TemplateSpec(
        "lyrics_video",
        "一首歌 + 依词 MV",
        "歌词 → 依词生视频；跳过封面。视频画面由平台自动引用歌词结果。",
        ["lyrics.generate", "video.generate"],
        [*_LYRICS_SLOTS, *_MV_SLOTS],
        inject={"video.generate": "MV 画面需配合以下歌词："},
    ),
    "full_mv": TemplateSpec(
        "full_mv",
        "一首歌 + 封面 + MV 全链",
        "歌词 → 依词封面 → MV。封面自动引用歌词；视频不接图引用（纯文生视频），"
        "MV 的歌曲意境由助手在 mv_prompt 槽位里写足。",
        ["lyrics.generate", "image.generate", "video.generate"],
        [*_LYRICS_SLOTS, *_COVER_SLOTS, *_MV_SLOTS],
        inject={"image.generate": "请围绕以下歌词的意境创作视觉画面："},
    ),
    "image_anim": TemplateSpec(
        "image_anim",
        "一张图 → 让它动起来",
        "图生视频：把已生成/上传的图片作为首帧，加镜头运动描进行动画化。"
        "用户诉求『把这张图/封面动起来』『让某资产变视频』的唯一入口。",
        ["video.generate"],
        [(name, spec) for name, spec in _I2V_SLOTS.items()],
    ),
    "cover_mv": TemplateSpec(
        "cover_mv",
        "一首歌 + 依词封面 + 封面动起来",
        "歌词 → 依词独动封面 → 用这张封面做首帧的 MV。当用户明说想『封面动起来』或"
        "要 MV 和封面画面一致时使用，MV 成员得声明『这是依照封面生成的』。",
        ["lyrics.generate", "image.generate", "video.generate"],
        [*_LYRICS_SLOTS, *_COVER_SLOTS, *_MV_SLOTS],
        inject={"image.generate": "请围绕以下歌词的意境创作视觉画面："},
        asset_ref={"video.generate": "source_asset"},
    ),
}


def get_template(template_id: str) -> TemplateSpec | None:
    return WORKFLOW_TEMPLATES.get(template_id)


_ATOMIC_PIVOT = "lyrics_only"
# 原子视觉模板的槽位 → 复合模板带前缀槽位
_ATOMIC_VISUAL_SLOTS = {
    "image_only": {
        "prompt": "cover_prompt",
        "style": "cover_style",
        "ratio": "cover_ratio",
        "palette": "cover_palette",
    },
    "video_only": {
        "prompt": "mv_prompt",
        "style": "mv_style",
        "ratio": "mv_ratio",
        "duration": "mv_duration",
    },
}
_COMPOSITE_FOR = {
    frozenset({"image_only"}): "lyrics_cover",
    frozenset({"video_only"}): "lyrics_video",
    frozenset({"image_only", "video_only"}): "full_mv",
}


def coalesce_dependency_specs(
    specs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """模型把「依词生图/MV」拆成 lyrics_only + 视觉原子的同批声明时，纠正为复合模板。

    起因（2026-10-11 评测实锤）：GLM-flash 面对「请改用复合模板」的拒绝文案，
    连着 5 次原样重试同一个非法荷载——指令纠正这条路被堵死。正确性只能靠平台
    确定性重写兜底，并在回执里明说改写动作（模型需如实转述）。

    只在组合无歧义时动手：恰好一条 lyrics_only，image/video 各至多一条。
    返回（改写后的 specs, 改写说明 | None）。
    """
    by_id: dict[str, list[dict[str, Any]]] = {}
    others: list[dict[str, Any]] = []
    for spec in specs:
        template_id = str(spec.get("template_id") or "")
        if template_id in _ATOMIC_VISUAL_SLOTS or template_id == _ATOMIC_PIVOT:
            by_id.setdefault(template_id, []).append(spec)
        else:
            others.append(spec)
    lyrics_specs = by_id.get(_ATOMIC_PIVOT, [])
    visuals = {tid: lst for tid, lst in by_id.items() if tid != _ATOMIC_PIVOT and lst}
    if (
        len(lyrics_specs) != 1
        or not visuals
        or any(len(lst) > 1 for lst in visuals.values())
    ):
        return specs, None
    composite_id = _COMPOSITE_FOR.get(frozenset(visuals))
    if composite_id is None:
        return specs, None

    merged_slots = dict(lyrics_specs[0].get("slots") or {})
    for template_id, visual_specs in visuals.items():
        mapping = _ATOMIC_VISUAL_SLOTS[template_id]
        for slot_name, value in dict(visual_specs[0].get("slots") or {}).items():
            merged_slots[mapping.get(slot_name, slot_name)] = value
    merged_spec: dict[str, Any] = {"template_id": composite_id, "slots": merged_slots}
    label_parts = [
        str(spec.get("label") or "").strip()
        for spec in lyrics_specs + [v[0] for v in visuals.values()]
    ]
    label = " + ".join(part for part in label_parts if part)[:30]
    if label:
        merged_spec["label"] = label
    note = (
        f"平台检测到依词构图意图，已把 lyrics_only 与 "
        f"{' + '.join(sorted(visuals))} 合并为复合模板"
        f"「{WORKFLOW_TEMPLATES[composite_id].name}」——封面 / MV 才能引用到歌词结果。"
    )
    return [merged_spec, *others], note


def describe_templates() -> list[dict[str, Any]]:
    """给 list_workflow_templates 工具：模板用途与槽位的人性化清单。"""
    described = []
    for spec in WORKFLOW_TEMPLATES.values():
        described.append(
            {
                "template_id": spec.template_id,
                "name": spec.name,
                "description": spec.description,
                "chain": [b.split(".")[0] for b in spec.blocks],
                "slots": [
                    {
                        "name": slot_name,
                        "label": label,
                        "required": required,
                        "default": default,
                    }
                    for slot_name, (_cap, _param, default, required, label) in spec.slots
                ],
            }
        )
    return described


def build_chain_params(
    template: TemplateSpec, slots: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """把槽位值展开成链上每个 Block 的参数包。

    返回 (params_list | None, error)；error 为人话校验信息（缺必填槽位等），
    枚举/长度边界留给 schemas 的 pydantic 模型在 service 层最终校验。
    """
    by_capability: dict[str, dict[str, Any]] = {cap: {} for cap in template.blocks}
    for slot_name, (capability_id, param_name, default, required, label) in template.slots:
        value = slots.get(slot_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if required and default is None:
                return None, f"模板「{template.name}」缺少必填槽位 {slot_name}（{label}）"
            value = default
        by_capability[capability_id][param_name] = value
    # 依赖注入：复合模板的下游 prompt = 槽位文本 + 引用前导句 + 上游引用记号
    params_list: list[dict[str, Any]] = []
    for capability_id in template.blocks:
        params = dict(by_capability[capability_id])
        lead_in = template.inject.get(capability_id)
        if lead_in and "prompt" in params:
            params["prompt"] = f"{params['prompt']}\n\n{lead_in}\n{UPSTREAM_REF}"
        asset_param = template.asset_ref.get(capability_id)
        if asset_param:
            params[asset_param] = UPSTREAM_ASSET_REF
        params_list.append(params)
    return params_list, None
