"""远端自托管 MiniMax H3 Gateway Provider。

Gateway 使用 FishStudio 自有的稳定异步合同，不伪装 MiniMax 云 API：
POST /v1/generations -> GET/DELETE /v1/generations/{task_id} -> artifact URL。
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.providers.base import ProviderResult, VideoGenerationRequest, VideoProvider

BASE_DIR = Path(__file__).parent.parent.parent
VIDEOS_DIR = BASE_DIR / "storage" / "videos"


def local_h3_enabled() -> bool:
    return os.getenv("LOCAL_H3_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


class LocalH3Provider(VideoProvider):
    """调用可信远端 H3 Gateway 的视频 Provider。"""

    name = "local_h3"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model_name: str | None = None,
        deployment_name: str | None = None,
        config: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ):
        config = config or {}
        capabilities = capabilities or {}
        self.enabled = local_h3_enabled() if enabled is None else enabled
        self.base_url = (
            (base_url if base_url is not None else os.getenv("LOCAL_H3_BASE_URL", "")).strip().rstrip("/")
        )
        self.api_key = (api_key if api_key is not None else os.getenv("LOCAL_H3_API_KEY", "")).strip()
        self.model_name = (model_name or os.getenv("LOCAL_H3_MODEL", "MiniMax-H3")).strip()
        self.deployment_name = (
            deployment_name or os.getenv("LOCAL_H3_DEPLOYMENT", "h3-fl2va-int8-convrot")
        ).strip()
        configured_modes = capabilities.get("modes")
        self.modes = (
            set(configured_modes)
            if configured_modes
            else {item.strip() for item in os.getenv("LOCAL_H3_MODES", "text").split(",") if item.strip()}
        )
        self.min_duration = int(config.get("min_duration", os.getenv("LOCAL_H3_MIN_DURATION", "4")))
        self.max_duration = int(config.get("max_duration", os.getenv("LOCAL_H3_MAX_DURATION", "6")))
        self.timeout = float(config.get("timeout_seconds", os.getenv("LOCAL_H3_TIMEOUT_SECONDS", "30")))
        self.artifact_timeout = float(
            config.get("artifact_timeout_seconds", os.getenv("LOCAL_H3_ARTIFACT_TIMEOUT_SECONDS", "900"))
        )
        self.max_artifact_bytes = int(
            config.get(
                "max_artifact_bytes",
                os.getenv("LOCAL_H3_MAX_ARTIFACT_BYTES", str(512 * 1024 * 1024)),
            )
        )
        self._max_concurrency = int(config.get("max_concurrency", os.getenv("LOCAL_H3_MAX_CONCURRENCY", "1")))

    @property
    def max_concurrency(self) -> int:
        return max(1, self._max_concurrency)

    def is_available(self) -> bool:
        return self.enabled and bool(self.base_url and self.api_key)

    def supports(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> bool:
        if not self.is_available() or (model and model != self.model_name):
            return False
        return (
            req.mode in self.modes
            and self.min_duration <= req.duration <= self.max_duration
            and not req.reference_videos
            and not req.reference_audios
            and not req.source_video
        )

    def resolution_for(self, req: VideoGenerationRequest, model: str = "") -> str:
        # 16GB Deployment 的实际画布由 Gateway 能力配置决定；此字段是路由档位。
        return "preview" if req.quality == "preview" else "standard"

    def _headers(self, *, idempotency_key: str = "") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    @staticmethod
    def _image_reference(path: str, role: str) -> dict[str, Any]:
        if path.startswith(("https://", "http://", "data:")):
            return {"type": "image", "role": role, "url": path}
        file_path = Path(path)
        if path.startswith("/storage/"):
            file_path = BASE_DIR / path.lstrip("/")
        elif not file_path.is_file():
            file_path = BASE_DIR / "storage" / path
        data = file_path.read_bytes()
        max_inline = int(os.getenv("LOCAL_H3_MAX_INLINE_IMAGE_BYTES", str(8 * 1024 * 1024)))
        if len(data) > max_inline:
            raise ValueError("本地参考图片超过内联上限，请先上传对象存储并使用签名 URL")
        suffix = file_path.suffix.lower().lstrip(".") or "jpeg"
        if suffix == "jpg":
            suffix = "jpeg"
        url = f"data:image/{suffix};base64,{base64.b64encode(data).decode('ascii')}"
        return {"type": "image", "role": role, "url": url}

    def _payload(self, req: VideoGenerationRequest, model: str) -> dict[str, Any]:
        references: list[dict[str, Any]] = []
        if req.first_frame:
            references.append(self._image_reference(req.first_frame, "first_frame"))
        if req.last_frame:
            references.append(self._image_reference(req.last_frame, "last_frame"))
        for item in req.reference_images:
            references.append(self._image_reference(item, "reference_image"))
        return {
            "deployment": self.deployment_name,
            "model": model,
            "prompt": req.prompt,
            "mode": req.mode,
            "duration": req.duration,
            "resolution": self.resolution_for(req, model),
            "aspect_ratio": req.aspect_ratio,
            "quality": req.quality,
            "references": references,
        }

    @staticmethod
    def _http_error(error: Exception) -> ProviderResult:
        if isinstance(error, httpx.TimeoutException):
            return ProviderResult(
                success=False,
                provider="local_h3",
                error_code="timeout",
                error_message=str(error),
                retryable=True,
            )
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            if status in {401, 403}:
                code, retryable = "auth_failed", False
            elif status == 429:
                code, retryable = "queue_full", True
            elif status >= 500:
                code, retryable = "gateway_unavailable", True
            else:
                code, retryable = "invalid_request", False
            return ProviderResult(
                success=False,
                provider="local_h3",
                error_code=code,
                error_message=str(error),
                retryable=retryable,
                raw_metadata={"status_code": status},
            )
        return ProviderResult(
            success=False,
            provider="local_h3",
            error_code="gateway_unavailable",
            error_message=str(error),
            retryable=True,
        )

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/health", headers=self._headers())
            response.raise_for_status()
            return response.json()

    async def submit(
        self,
        req: VideoGenerationRequest,
        *,
        model: Optional[str] = None,
    ) -> ProviderResult:
        locked_model = model or self.model_name
        if not self.supports(req, model=locked_model):
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="unsupported_request",
                error_message="当前 Local H3 Deployment 不支持该模式、时长或参考素材",
            )
        try:
            payload = self._payload(req, locked_model)
            request_key = req.idempotency_key or str(uuid.uuid4())
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/v1/generations",
                    headers=self._headers(idempotency_key=request_key),
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, OSError, ValueError) as error:
            result = self._http_error(error)
            if isinstance(error, ValueError):
                result.error_code = "invalid_input"
                result.retryable = False
            result.model = locked_model
            return result
        task_id = str(data.get("task_id") or "")
        if not task_id:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="invalid_gateway_response",
                error_message="Gateway 响应缺少 task_id",
                retryable=True,
                raw_metadata=data,
            )
        return ProviderResult(
            success=True,
            provider=self.name,
            model=locked_model,
            external_request_id=task_id,
            usage={"output_video_seconds": float(req.duration)},
            raw_metadata={"protocol": "local_h3_v1", "deployment": self.deployment_name},
        )

    async def _task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/v1/generations/{task_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_status(
        self,
        external_task_id: str,
        *,
        protocol: str | None = None,
    ) -> ProviderResult:
        try:
            data = await self._task(external_task_id)
        except httpx.HTTPError as error:
            result = self._http_error(error)
            result.external_request_id = external_task_id
            return result
        status = str(data.get("status") or "").lower()
        if status == "succeeded":
            return ProviderResult(
                success=True,
                provider=self.name,
                model=self.model_name,
                external_request_id=external_task_id,
                raw_metadata=data,
            )
        # submitting：Gateway 正在把任务交给 ComfyUI（含上传首尾帧），属于进行中
        if status in {"queued", "submitting", "running", "processing"}:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=self.model_name,
                external_request_id=external_task_id,
                error_code="pending",
                error_message=f"Local H3 任务进行中: {status}",
                retryable=True,
                raw_metadata=data,
            )
        error = data.get("error") or {}
        if isinstance(error, dict):
            code = str(error.get("code") or "task_failed")
            message = str(error.get("message") or f"Local H3 任务状态异常: {status or 'unknown'}")
        else:
            code, message = "task_failed", str(error)
        return ProviderResult(
            success=False,
            provider=self.name,
            model=self.model_name,
            external_request_id=external_task_id,
            error_code=code or "task_failed",
            error_message=message[:500],
            retryable=code in {"queue_full", "timeout"},
            raw_metadata=data,
        )

    def _validate_artifact_url(self, url: str) -> None:
        parsed = urlparse(url)
        base = urlparse(self.base_url)
        allowed = {
            item.strip().lower()
            for item in os.getenv("LOCAL_H3_ARTIFACT_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        }
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("视频 Artifact URL 协议或主机无效")
        if (
            parsed.hostname.lower() != (base.hostname or "").lower()
            and parsed.hostname.lower() not in allowed
        ):
            raise ValueError("视频 Artifact URL 主机不在允许列表")

    async def cancel(self, external_task_id: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.delete(
                    f"{self.base_url}/v1/generations/{external_task_id}",
                    headers=self._headers(),
                )
            return response.status_code in {200, 202, 204, 409}
        except httpx.HTTPError:
            return False

    async def fetch_result(
        self,
        external_task_id: str,
        *,
        protocol: str | None = None,
    ) -> ProviderResult:
        try:
            data = await self._task(external_task_id)
            if str(data.get("status") or "").lower() != "succeeded":
                return ProviderResult(
                    success=False,
                    provider=self.name,
                    model=self.model_name,
                    external_request_id=external_task_id,
                    error_code="pending",
                    error_message="Local H3 任务尚未完成",
                    retryable=True,
                )
            artifacts = data.get("artifacts") or []
            video = next((item for item in artifacts if item.get("type") == "video"), None)
            if not video or not video.get("url"):
                raise ValueError("Gateway 成功响应中没有视频 Artifact URL")
            self._validate_artifact_url(str(video["url"]))
            async with httpx.AsyncClient(timeout=self.artifact_timeout) as client:
                response = await client.get(str(video["url"]), headers=self._headers())
                response.raise_for_status()
                content = response.content
                content_type = response.headers.get("content-type", "").split(";", 1)[0]
            if content_type and content_type not in {"video/mp4", "application/octet-stream"}:
                raise ValueError(f"视频 Artifact MIME 不合法: {content_type}")
            if not content or len(content) > self.max_artifact_bytes:
                raise ValueError("视频 Artifact 为空或超过大小限制")
            expected_sha = str(video.get("sha256") or "")
            actual_sha = hashlib.sha256(content).hexdigest()
            if expected_sha and expected_sha.lower() != actual_sha:
                raise ValueError("视频 Artifact SHA-256 校验失败")
        except (httpx.HTTPError, ValueError) as error:
            result = self._http_error(error)
            if isinstance(error, ValueError):
                result.error_code = "artifact_invalid"
                result.retryable = False
            result.model = self.model_name
            result.external_request_id = external_task_id
            return result

        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"local_h3_{external_task_id}_{uuid.uuid4().hex[:8]}.mp4"
        path = VIDEOS_DIR / filename
        path.write_bytes(content)
        return ProviderResult(
            success=True,
            provider=self.name,
            model=self.model_name,
            external_request_id=external_task_id,
            artifacts=[
                {
                    "type": "video",
                    "storage_key": f"videos/{filename}",
                    "duration_ms": video.get("duration_ms"),
                    "width": video.get("width"),
                    "height": video.get("height"),
                    "sha256": actual_sha,
                }
            ],
            usage=data.get("usage") or {},
            raw_metadata=data,
        )
