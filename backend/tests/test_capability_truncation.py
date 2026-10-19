"""超长入参宽容截断层（D 类真机实证：整段歌词灌进 Kolors prompt 300 字上限会整链必炸）。

语义约定：
- 只有「命中 max_length 的字符串字段」被截到上限；截完仍不合法（其它字段错/空串）→ 原口径失败；
- 截断事件落 WARNING 日志（capability_input_truncated），不静默；
- 成功路径的调用方（图像/视频/歌词均同款）不再被拒——链能跑完。
"""

import asyncio
from dataclasses import replace

from schemas.capability import ImageGenerateInput
from services.capabilities import runtime
from services.capabilities.runtime import CapabilityContext, _truncate_overlong_strings


def _ctx() -> CapabilityContext:
    return CapabilityContext(user_id=1, project_id=None, source="workflow")


class TestTruncateHelper:
    def test_overlong_string_is_cut_to_field_limit(self):
        fixed = _truncate_overlong_strings(
            ImageGenerateInput,
            {"prompt": "字" * 400, "style": "电影概念艺术", "ratio": "1:1", "palette": "深蓝与紫色"},
        )
        assert fixed is not None
        assert len(fixed.prompt) == 300

    def test_not_overlong_returns_none(self):
        """没有字段越限时助手是「无需施救」——返回 None（生产路径：这种入参本不会进这里，
        helper 只在首轮 model_validate 败后被调用）。"""
        assert _truncate_overlong_strings(
            ImageGenerateInput,
            {"prompt": "海边黄昏", "style": "电影概念艺术", "ratio": "1:1", "palette": "深蓝与紫色"},
        ) is None

    def test_non_length_errors_fail_unmodified(self):
        # ratio 非法值不是长度问题，截断救不回来——必须如实失败
        assert (
            _truncate_overlong_strings(
                ImageGenerateInput,
                {"prompt": "海边黄昏", "style": "电影概念艺术", "ratio": "3:1(假)", "palette": "深蓝与紫色"},
            )
            is None
        )


class TestRunCapabilityIntegration:
    def test_overlong_prompt_reaches_executor_truncated(self, monkeypatch):
        """runtime 级：长 prompt 不再被「参数不符合平台要求」轰走，而是截断后到达执行器。"""
        entry = runtime.CAPABILITY_REGISTRY["image.generate"]
        reached: dict[str, int] = {}

        def spy_executor(validated):
            reached["prompt_len"] = len(validated.prompt)
            return {"format": "image", "content": "/api/assets/fake.png"}

        monkeypatch.setitem(
            runtime.CAPABILITY_REGISTRY, "image.generate", replace(entry, executor=spy_executor)
        )
        result = asyncio.run(
            runtime.run_capability(
                "image.generate",
                {"prompt": "歌词" * 200, "style": "电影概念艺术", "ratio": "1:1", "palette": "深蓝与紫色"},
                context=_ctx(),
            )
        )
        assert result.status == "succeeded"
        assert reached["prompt_len"] == 300

    def test_truly_invalid_params_still_fail(self, monkeypatch):
        def never_executor(validated):  # pragma: no cover - 不该走到
            raise AssertionError("非法参数不该到达执行器")

        entry = runtime.CAPABILITY_REGISTRY["image.generate"]
        monkeypatch.setitem(
            runtime.CAPABILITY_REGISTRY, "image.generate", replace(entry, executor=never_executor)
        )
        result = asyncio.run(
            runtime.run_capability(
                "image.generate",
                {"prompt": "", "style": "电影概念艺术", "ratio": "1:1", "palette": "深蓝与紫色"},
                context=_ctx(),
            )
        )
        assert result.status == "failed"
        assert "平台要求" in (result.error or "")
