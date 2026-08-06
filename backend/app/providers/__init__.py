"""Provider 抽象层：统一的生成类外部服务接口 + 路由 + 价格表。"""

from app.providers.base import ProviderResult, VideoGenerationRequest, VideoProvider

__all__ = ["ProviderResult", "VideoGenerationRequest", "VideoProvider"]
