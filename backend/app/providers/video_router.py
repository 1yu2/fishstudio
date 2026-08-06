"""视频 provider 路由：按 provider_policy 选择 provider，并给出价格快照。"""

import logging
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.config import mock_providers_enabled
from app.providers.base import VideoGenerationRequest, VideoProvider
from app.providers.minimax_video import MiniMaxVideoProvider
from app.providers.mock_video import MockVideoProvider
from app.providers.pricing import estimate_video_cost, snapshot_price
from app.providers.volcano_video import VolcanoVideoProvider

logger = logging.getLogger(__name__)


class NoProviderError(RuntimeError):
    """无可用真实 provider 且未允许 mock 兜底（error_code="no_provider"，不可重试）。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.error_code = "no_provider"


class RouteDecision(BaseModel):
    """一次路由决策（含理由与价格快照，供写回 job.params 审计）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: VideoProvider
    model: str = ""
    resolution: str = ""
    price_snapshot: dict = {}
    mocked: bool = False
    raw_reason: str = ""


class VideoProviderRouter:
    """按 provider_policy 路由视频生成请求。"""

    def __init__(self, providers: Optional[list[VideoProvider]] = None):
        if providers is None:
            providers = [MiniMaxVideoProvider(), VolcanoVideoProvider(), MockVideoProvider()]
        self.providers = providers
        self._mock = next(
            (p for p in providers if isinstance(p, MockVideoProvider)), MockVideoProvider()
        )

    def _model_and_resolution(self, provider: VideoProvider, req) -> tuple[str, str]:
        if isinstance(provider, MiniMaxVideoProvider):
            return provider.pick_model(req), provider.resolution_for(req)
        if isinstance(provider, VolcanoVideoProvider):
            from app.tools import volcano_video_generation as vvg

            return vvg.ACTIVE_VIDEO_MODEL, req.resolution
        return "mock-video", req.resolution

    def _decide(self, provider: VideoProvider, req, mocked: bool, reason: str) -> RouteDecision:
        model, resolution = self._model_and_resolution(provider, req)
        snapshot = {} if mocked else snapshot_price(provider.name, model, resolution)
        logger.info(f"视频路由: {provider.name}/{model} ({reason})")
        return RouteDecision(
            provider=provider,
            model=model,
            resolution=resolution,
            price_snapshot=snapshot,
            mocked=mocked,
            raw_reason=reason,
        )

    def select(self, req: VideoGenerationRequest) -> RouteDecision:
        """路由：explicit -> supports/available 过滤 -> policy 排序 -> mock 兜底。"""
        # 1) 显式指定：按名字找，不可用直接报错（不悄悄换 provider）
        if req.provider_policy == "explicit" or req.explicit_provider:
            name = req.explicit_provider or ""
            chosen = next((p for p in self.providers if p.name == name), None)
            if chosen is None:
                raise ValueError(f"显式指定的 provider 不存在: {name}")
            if not chosen.is_available() or not chosen.supports(req):
                raise ValueError(f"显式指定的 provider 不可用或不支持该请求: {name}")
            return self._decide(chosen, req, False, f"explicit provider={name}")

        # 2) 过滤 supports + is_available（mock 不参与正常候选，只兜底）
        candidates = [
            p
            for p in self.providers
            if not isinstance(p, MockVideoProvider) and p.supports(req) and p.is_available()
        ]

        # 3) policy 排序
        if req.provider_policy == "cheapest":
            def _cost(p: VideoProvider) -> float:
                model, resolution = self._model_and_resolution(p, req)
                cost = estimate_video_cost(
                    p.name, model, resolution, output_seconds=req.duration
                )
                return float(cost) if cost is not None else float("inf")

            candidates.sort(key=_cost)
            reason = "cheapest: 按估算成本升序"
        elif req.provider_policy == "quality":
            # volcano 优先，其次 minimax（H3 可按 req.resolution=2K 出高质量）
            candidates.sort(key=lambda p: 0 if isinstance(p, VolcanoVideoProvider) else 1)
            reason = "quality: volcano 优先，其次 minimax"
        else:  # balanced（默认）
            # 路由守卫（复审 0.9）：MiniMax H3 协议未经官方文档核验，balanced 默认
            # volcano 优先，minimax 殿后——且此时 minimax 只会以已核验的 Hailuo V1
            # 系列模型出现在候选（H3 实验关闭时 supports/pick_model 不选 H3）。
            # MINIMAX_H3_EXPERIMENTAL=true 时恢复原 minimax(H3) 优先。
            from app.providers.minimax_video import h3_experimental_enabled

            if h3_experimental_enabled():
                candidates.sort(
                    key=lambda p: (
                        0
                        if isinstance(p, MiniMaxVideoProvider)
                        else 1
                        if isinstance(p, VolcanoVideoProvider)
                        else 2
                    )
                )
                reason = (
                    "balanced: minimax(H3, MINIMAX_H3_EXPERIMENTAL=true 实验分支，"
                    "协议未经官方文档核验) 优先，其次 volcano"
                )
            else:
                candidates.sort(
                    key=lambda p: (
                        0
                        if isinstance(p, VolcanoVideoProvider)
                        else 1
                        if isinstance(p, MiniMaxVideoProvider)
                        else 2
                    )
                )
                reason = (
                    "balanced: volcano 优先，其次 minimax（仅已核验的 Hailuo V1 系列；"
                    "H3 默认禁用，见 MINIMAX_H3_EXPERIMENTAL）"
                )

        # 4) 无可用真实 provider：仅 ALLOW_MOCK_PROVIDERS=true 才允许 mock 兜底；
        # 否则抛 NoProviderError（worker 侧标 no_provider 不可重试失败）
        if not candidates:
            if not mock_providers_enabled():
                raise NoProviderError(
                    f"无可用 provider（policy={req.provider_policy}），"
                    "且未开启 ALLOW_MOCK_PROVIDERS，拒绝 mock 兜底"
                )
            return self._decide(
                self._mock, req, True, f"无可用 provider（policy={req.provider_policy}），兜底 mock"
            )
        return self._decide(candidates[0], req, False, reason)
