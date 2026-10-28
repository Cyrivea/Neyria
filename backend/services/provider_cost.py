"""上游 Provider 真实成本估算（I3）：平台为一次调用实际付给上游的钱。

单位约定：人民币 **micro 元**（1 元 = 1_000_000 micro），与账务体系的 micro 约定同源，
二进制浮点不进结果。

定价来源（2026-10-03 人工核对供应商公开定价页）：
- 智谱 glm-4-flash：免费档 → 0
- SiliconFlow BAAI/bge-m3：免费 → 0
- SiliconFlow Kwai-Kolors/Kolors：免费 → 0
- SiliconFlow Wan-AI/Wan2.2-T2V-A14B：¥2.00/次（5 秒档；时长差异按实际账单回填校准）

换源/调价不用改代码：PROVIDER_COST_OVERRIDES_JSON 环境变量覆盖，例如
    {"Wan-AI/Wan2.2-T2V-A14B": {"kind": "per_call", "yuan": "1.50"}}

注意：这里是「估算口径」，为成本核算和毛利观察服务；与「向用户收多少」
（model_price_versions 的价格版本）是完全独立的两套数，不许混用。
未收录的模型返回 None——宁缺不造假：provider_cost_micro 列保持 NULL 表示「未校准」。
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from core import config

logger = logging.getLogger("provider_cost")

MICRO_PER_CNY = 1_000_000

_PRICES: dict[str, dict[str, Decimal | str]] = {
    "glm-4-flash": {"kind": "token", "input_yuan_per_1k": Decimal("0"), "output_yuan_per_1k": Decimal("0")},
    "BAAI/bge-m3": {"kind": "token", "input_yuan_per_1k": Decimal("0"), "output_yuan_per_1k": Decimal("0")},
    "Kwai-Kolors/Kolors": {"kind": "per_call", "yuan": Decimal("0")},
    "Wan-AI/Wan2.2-T2V-A14B": {"kind": "per_call", "yuan": Decimal("2.00")},
    # 本地 ComfyUI LTX-Video 自建算力：上游云端成本≈0（电费不入账）
    # 但显式记 0 而非 NULL——「上线过且成本为 0」与「未校准」是两种不同会计语义
    "LTX-Video-2B-distilled/local": {"kind": "per_call", "yuan": Decimal("0")},
}


def _overrides() -> dict[str, dict]:
    """环境覆盖表：解析失败静默丢弃并告警——成本估算失败不许拖垮主链路。"""
    raw = config.PROVIDER_COST_OVERRIDES_JSON.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        logger.warning("PROVIDER_COST_OVERRIDES_JSON 解析失败，忽略覆盖", extra={"evt": "provider_cost_override_bad"})
        return {}


def estimate_cost_micro(model: str | None, prompt_tokens: int = 0, completion_tokens: int = 0) -> int | None:
    """估算一次调用的上游成本（micro 元）。模型未收录返回 None（列保持 NULL=未校准）。"""
    if not model:
        return None
    entry = _overrides().get(model) or _PRICES.get(model)
    if entry is None:
        return None
    kind = entry.get("kind")
    if kind == "per_call":
        return int(Decimal(str(entry["yuan"])) * MICRO_PER_CNY)
    if kind == "token":
        yuan = (
            Decimal(prompt_tokens) * Decimal(str(entry["input_yuan_per_1k"]))
            + Decimal(completion_tokens) * Decimal(str(entry["output_yuan_per_1k"]))
        ) / Decimal(1000)
        return int(yuan * MICRO_PER_CNY)
    return None
