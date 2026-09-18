"""Model Runtime 的分模态协议。"""

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class MediaModelRequest:
    prompt: str = ""
    references: tuple[str, ...] = ()
    duration_ms: Optional[int] = None
    resolution: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResult:
    success: bool
    provider_request_id: str = ""
    artifacts: tuple[dict[str, Any], ...] = ()
    usage: dict[str, float] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    raw_metadata: dict[str, Any] = field(default_factory=dict)


class ImageModelDriver(Protocol):
    async def generate(self, request: MediaModelRequest) -> ModelResult: ...


class VideoModelDriver(Protocol):
    async def submit(self, request: MediaModelRequest) -> ModelResult: ...
    async def query(self, provider_request_id: str) -> ModelResult: ...
    async def cancel(self, provider_request_id: str) -> bool: ...
    async def fetch(self, provider_request_id: str) -> ModelResult: ...


class SpeechModelDriver(Protocol):
    async def synthesize(self, request: MediaModelRequest) -> ModelResult: ...


class MultimodalReviewDriver(Protocol):
    async def review(self, request: MediaModelRequest) -> ModelResult: ...
