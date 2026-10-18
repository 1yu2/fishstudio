"""统一模型运行时：业务按 purpose/capability 获取模型，不直接绑定厂商。"""

from app.model_runtime.catalog import CatalogSnapshot, get_catalog
from app.model_runtime.core import (
    ChatDeployment,
    ChatModelRequest,
    ModelCapability,
    ModelInvocationResult,
    ModelPolicy,
    ModelRequestContext,
    ModelRuntime,
    RoutingSnapshot,
    get_model_runtime,
)
from app.model_runtime.drivers import (
    ImageModelDriver,
    MediaModelRequest,
    ModelResult,
    MultimodalReviewDriver,
    SpeechModelDriver,
    VideoModelDriver,
)
from app.model_runtime.registry import DriverRegistry

__all__ = [
    "ChatDeployment",
    "ChatModelRequest",
    "ModelCapability",
    "ModelInvocationResult",
    "ModelPolicy",
    "ModelRequestContext",
    "ModelRuntime",
    "RoutingSnapshot",
    "get_model_runtime",
    "ImageModelDriver",
    "MediaModelRequest",
    "ModelResult",
    "MultimodalReviewDriver",
    "SpeechModelDriver",
    "VideoModelDriver",
    "CatalogSnapshot",
    "DriverRegistry",
    "get_catalog",
]
