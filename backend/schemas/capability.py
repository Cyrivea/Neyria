"""生产能力请求与结果 Schema。"""

from typing import Literal

from pydantic import BaseModel, Field

LyricsStyle = str  # 自由文本（不限枚举）；前端给推荐值，大不了说人话
LyricsMood = str
LyricsLanguage = Literal["中文", "英文", "中英混合"]  # 语言是路由选择，仍关枚举

ImageStyle = str  # 同理：创作者的感性词汇不设防
ImageRatio = Literal["16:9", "1:1", "9:16", "4:3"]  # 上游画幅是真参数
ImagePalette = str

VideoStyle = str
VideoRatio = Literal["16:9", "1:1", "9:16"]
VideoDuration = Literal["5 秒", "10 秒"]  # 上游 API 的真实支持集，不可抴


class LyricsGenerateInput(BaseModel):
    """歌词 Block 的受控输入；前后端和 Assistant 共用。"""

    theme: str = Field(..., min_length=1, max_length=200)
    style: LyricsStyle = Field("流行抒情", max_length=60)
    mood: LyricsMood = Field("温柔、克制", max_length=60)
    language: LyricsLanguage = "中文"


class ImageGenerateInput(BaseModel):
    """图像 Block 的受控输入；不接真实图片 API 时也必须具备同样的校验边界。"""

    prompt: str = Field(..., min_length=1, max_length=300)
    style: ImageStyle = Field("电影概念艺术", max_length=80)
    ratio: ImageRatio = "16:9"
    palette: ImagePalette = Field("深蓝与紫色", max_length=80)


class VideoGenerateInput(BaseModel):
    """视频 Block 的受控输入；与前端面板共用校验边界。

    双形态（2026-10-28，solved #137）：
    - 文生视频（T2V）：不给 source_asset，纯提示词驱动；
    - 图生视频（I2V）：给 source_asset（资产 id `asset-xxx` 或资产 URL `/api/assets/xx.jpg`
      都接受），上游画面作为首帧引导。运行时做属主校验；Provider 不支持 I2V 时如实拒绝。
    """

    prompt: str = Field(..., min_length=1, max_length=500)
    style: VideoStyle = Field("电影写实", max_length=80)
    ratio: VideoRatio = "16:9"
    duration: VideoDuration = "5 秒"
    source_asset: str | None = Field(None, min_length=4, max_length=160)


class CapabilityRunRequest(BaseModel):
    """独立运行入口：按 capability_id 白名单扩展，输入类型随场景。"""

    capability_id: Literal["lyrics.generate", "image.generate", "video.generate"]
    project_id: int | None = None
    inputs: LyricsGenerateInput | ImageGenerateInput | VideoGenerateInput
    # I12 客户端幂等键：一次"用户点击"生成一个 UUID；网络重试复用同一 key，
    # 后端 reservation 唯一索引防重复扣费/重复生成（Spec §3.5 第一层）
    idempotency_key: str | None = Field(None, min_length=8, max_length=128)


class CapabilityResult(BaseModel):
    capability_id: str
    status: Literal["succeeded", "model_unavailable", "failed"]
    result: dict[str, str] | None = None
    error: str | None = None
