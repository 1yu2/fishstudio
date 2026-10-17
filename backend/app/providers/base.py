"""Provider 抽象基类与统一结果/请求模型（文档 S6）。"""

from abc import ABC, abstractmethod
from typing import Literal, Optional

from pydantic import BaseModel, Field

# usage 语义（字段可选，缺省视为 0）：
#   output_video_seconds: 输出视频秒数
#   input_video_seconds: 输入（参考）视频秒数
#   input_image_count: 输入图片张数
#   input_audio_seconds: 输入音频秒数
#   regeneration_output_seconds: 再生成输出秒数
USAGE_KEYS = (
    "output_video_seconds",
    "input_video_seconds",
    "input_image_count",
    "input_audio_seconds",
    "regeneration_output_seconds",
)


class ProviderResult(BaseModel):
    """所有 provider 的统一返回结构。"""

    success: bool
    provider: str = ""
    model: str = ""
    external_request_id: str = ""
    # 每项: {storage_key, type, duration_ms?, width?, height?}
    artifacts: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)  # 语义见 USAGE_KEYS
    retryable: bool = False
    error_code: str = ""
    error_message: str = ""
    raw_metadata: dict = Field(default_factory=dict)


class VideoGenerationRequest(BaseModel):
    """视频生成统一请求（V2 多模态语义）。"""

    mode: Literal["text", "first_frame", "first_last_frame", "reference", "regenerate"] = "text"
    prompt: str
    first_frame: Optional[str] = None  # /storage/ 路径或本地路径
    last_frame: Optional[str] = None
    reference_images: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    reference_audios: list[str] = Field(default_factory=list)
    source_video: Optional[str] = None  # regenerate 模式的源视频
    duration: int = 6
    resolution: str = "768P"
    aspect_ratio: str = "9:16"
    quality: Literal["preview", "standard", "final"] = "standard"
    provider_policy: Literal["cheapest", "balanced", "quality", "explicit"] = "balanced"
    explicit_provider: Optional[str] = None
    # 由 Worker 写入，远端异步 Provider 必须据此去重。
    idempotency_key: Optional[str] = None


class VideoProvider(ABC):
    """视频生成 provider 抽象基类。

    生命周期: submit -> (get_status 轮询) -> fetch_result。
    """

    name: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """provider 当前是否可用（缺 API key 等返回 False）。"""

    @abstractmethod
    def supports(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> bool:
        """是否支持该请求（模式/时长/分辨率等约束）。model 非空时检查锁定模型的能力。"""

    @abstractmethod
    async def submit(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> ProviderResult:
        """提交任务。external_request_id 填外部 task_id。

        model 非空时 Adapter 必须使用该锁定模型，不得执行内部路由/pick_model。
        """

    @abstractmethod
    async def get_status(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        """查询任务状态。

        进行中: success=False + error_code="pending" + retryable=True。
        protocol：MiniMax 区分 v1/h3 协议端点，其余 Adapter 忽略。
        """

    async def cancel(self, external_task_id: str) -> bool:
        """取消任务（可选实现，默认不支持）。"""
        return False

    @abstractmethod
    async def fetch_result(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        """下载产物到 backend/storage/videos/ 并返回含 storage_key 的 artifacts。
        protocol：MiniMax 区分 v1/h3 协议端点，其余 Adapter 忽略。"""
