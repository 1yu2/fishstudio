"""火山方舟（Volcano Ark）视频生成 Adapter：Seedance 系列。

协议：POST {base_url}/contents/generations/tasks 创建异步任务，GET .../tasks/{id} 查询，
DELETE .../tasks/{id} 取消排队中的任务。请求体为 model + content 数组：text 一项放提示词，
image_url 项通过 role 区分 first_frame / last_frame / reference_image。

方舟返回的 video_url / last_frame_url 约 24 小时后失效，所以任务成功后立即下载到本地存储。
全程使用 httpx 异步调用，不阻塞事件循环。
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.providers.base import ProviderResult, VideoGenerationRequest, VideoProvider

BASE_DIR = Path(__file__).parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage"
VIDEOS_DIR = STORAGE_DIR / "videos"
IMAGES_DIR = STORAGE_DIR / "images"

_PENDING_STATUSES = {"queued", "running"}
_RETRYABLE_TASK_STATUSES = {"expired"}  # 排队超时未执行，不产生费用，可安全重提
_ROLE_BY_FIELD = {"first_frame": "first_frame", "last_frame": "last_frame"}
_VIDEO_MIME = {"video/mp4", "application/octet-stream", "binary/octet-stream"}
_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp", "application/octet-stream", "binary/octet-stream"}


class ArkVideoProvider(VideoProvider):
    """Seedance via 火山方舟；一个实例对应一个 Deployment（一个模型）。"""

    name = "ark"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        deployment_name: str = "",
        config: Optional[dict[str, Any]] = None,
        capabilities: Optional[dict[str, Any]] = None,
        enabled: bool = True,
    ) -> None:
        config = config or {}
        capabilities = capabilities or {}
        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.model_name = model_name.strip()
        self.deployment_name = deployment_name or self.model_name
        self.enabled = enabled

        self.timeout = float(config.get("timeout_seconds", 60))
        self.artifact_timeout = float(config.get("artifact_timeout_seconds", 600))
        self.max_artifact_bytes = int(config.get("max_artifact_bytes", 512 * 1024 * 1024))
        self.max_inline_image_bytes = int(config.get("max_inline_image_bytes", 10 * 1024 * 1024))
        self.default_resolution = str(config.get("default_resolution", "720p"))
        self.preview_resolution = str(config.get("preview_resolution", "480p"))
        self.generate_audio = bool(config.get("generate_audio", False))
        self.watermark = bool(config.get("watermark", False))
        self.return_last_frame = bool(config.get("return_last_frame", True))
        self.camera_fixed = config.get("camera_fixed")
        self.service_tier = str(config.get("service_tier", "default"))
        self.execution_expires_after = config.get("execution_expires_after")
        self.callback_url = str(config.get("callback_url", "") or "")
        self.parameter_style = str(config.get("parameter_style", "fields"))
        self.artifact_allowed_host_suffixes = tuple(
            item.strip().lower().lstrip(".")
            for item in config.get("artifact_allowed_host_suffixes", []) or []
        )
        self._max_concurrency = int(config.get("max_concurrency", 2))

        self.modes = set(capabilities.get("modes") or ["text", "first_frame", "first_last_frame"])
        self.min_duration = int(capabilities.get("min_duration", 4))
        self.max_duration = int(capabilities.get("max_duration", 12))
        self.resolutions = set(capabilities.get("resolutions") or ["480p", "720p", "1080p"])
        self.aspect_ratios = set(
            capabilities.get("aspect_ratios") or ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"]
        )
        self.max_reference_images = int(capabilities.get("max_reference_images", 0))

    # ── 能力与路由 ─────────────────────────────────────────────────────────

    @property
    def max_concurrency(self) -> int:
        return max(1, self._max_concurrency)

    def is_available(self) -> bool:
        return self.enabled and bool(self.api_key and self.base_url and self.model_name)

    def resolution_for(self, req: VideoGenerationRequest, model: str = "") -> str:
        """draft/preview 档用低分辨率省钱，其余用默认分辨率；请求显式给出合法档位时尊重请求。"""
        requested = (req.resolution or "").lower()
        if requested in self.resolutions:
            return requested
        if req.quality == "preview":
            return self.preview_resolution
        return self.default_resolution

    def supports(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> bool:
        if not self.is_available() or (model and model != self.model_name):
            return False
        if req.mode not in self.modes:
            return False
        if not self.min_duration <= req.duration <= self.max_duration:
            return False
        if req.aspect_ratio not in self.aspect_ratios:
            return False
        if self.resolution_for(req) not in self.resolutions:
            return False
        if req.reference_videos or req.reference_audios or req.source_video:
            return False
        return len(req.reference_images) <= self.max_reference_images

    # ── 请求构造 ───────────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _image_url(self, path: str) -> str:
        """公网 URL / data URI 原样使用；本地存储路径或 storage_key 内联为 base64 data URI。"""
        if path.startswith(("https://", "http://", "data:")):
            return path
        if path.startswith("/storage/"):
            file_path = BASE_DIR / path.lstrip("/")
        else:
            file_path = STORAGE_DIR / path.lstrip("/")
        resolved = file_path.resolve()
        if not resolved.is_relative_to(STORAGE_DIR.resolve()):
            raise ValueError("参考图片路径必须位于 storage 目录内")
        data = resolved.read_bytes()
        if len(data) > self.max_inline_image_bytes:
            raise ValueError("参考图片超过内联上限，请先上传对象存储并使用签名 URL")
        mime = mimetypes.guess_type(resolved.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def _content(self, req: VideoGenerationRequest, resolution: str) -> list[dict[str, Any]]:
        prompt = req.prompt.strip()
        if self.parameter_style == "inline":
            # 早期 Seedance 模型只认提示词内的命令参数。
            flags = [
                f"--rs {resolution}",
                f"--rt {req.aspect_ratio}",
                f"--dur {req.duration}",
                f"--wm {str(self.watermark).lower()}",
            ]
            if self.camera_fixed is not None:
                flags.append(f"--cf {str(bool(self.camera_fixed)).lower()}")
            prompt = f"{prompt} {' '.join(flags)}".strip()
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for field, role in _ROLE_BY_FIELD.items():
            value = getattr(req, field)
            if value:
                content.append(
                    {"type": "image_url", "image_url": {"url": self._image_url(value)}, "role": role}
                )
        for item in req.reference_images:
            content.append(
                {"type": "image_url", "image_url": {"url": self._image_url(item)}, "role": "reference_image"}
            )
        return content

    def build_payload(self, req: VideoGenerationRequest, model: str) -> dict[str, Any]:
        resolution = self.resolution_for(req, model)
        payload: dict[str, Any] = {"model": model, "content": self._content(req, resolution)}
        if self.parameter_style != "inline":
            payload.update(
                {
                    "ratio": req.aspect_ratio,
                    "duration": req.duration,
                    "resolution": resolution,
                    "watermark": self.watermark,
                }
            )
            if self.camera_fixed is not None:
                payload["camera_fixed"] = bool(self.camera_fixed)
        payload["generate_audio"] = self.generate_audio
        payload["return_last_frame"] = self.return_last_frame
        if self.service_tier != "default":
            payload["service_tier"] = self.service_tier
        if self.execution_expires_after:
            payload["execution_expires_after"] = int(self.execution_expires_after)
        if self.callback_url:
            payload["callback_url"] = self.callback_url
        return payload

    # ── 错误分类 ───────────────────────────────────────────────────────────

    def _error(self, error: Exception, *, model: str = "", task_id: str = "") -> ProviderResult:
        base = {"success": False, "provider": self.name, "model": model or self.model_name}
        if task_id:
            base["external_request_id"] = task_id
        if isinstance(error, httpx.TimeoutException):
            return ProviderResult(**base, error_code="timeout", error_message=str(error), retryable=True)
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            detail = _error_detail(error.response)
            if status in {401, 403}:
                code, retryable = "auth_failed", False
            elif status == 429:
                code, retryable = "rate_limited", True
            elif status >= 500:
                code, retryable = "server_error", True
            else:
                code, retryable = "invalid_request", False
            return ProviderResult(
                **base,
                error_code=code,
                error_message=detail[:500],
                retryable=retryable,
                raw_metadata={"status_code": status},
            )
        if isinstance(error, httpx.HTTPError):
            return ProviderResult(
                **base, error_code="network_error", error_message=str(error), retryable=True
            )
        return ProviderResult(
            **base, error_code="invalid_input", error_message=str(error)[:500], retryable=False
        )

    # ── 生命周期 ───────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        """用任务列表接口验证地址与密钥；不创建任务、不产生费用。"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/contents/generations/tasks",
                headers=self._headers(),
                params={"page_num": 1, "page_size": 1},
            )
            response.raise_for_status()
        return {"available": True, "model": self.model_name}

    async def submit(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> ProviderResult:
        locked_model = model or self.model_name
        if not self.supports(req, model=locked_model):
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="unsupported_request",
                error_message="当前 Seedance Deployment 不支持该模式、时长、画幅或参考素材",
            )
        try:
            payload = self.build_payload(req, locked_model)
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/contents/generations/tasks", headers=self._headers(), json=payload
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, OSError, ValueError) as error:
            return self._error(error, model=locked_model)
        task_id = str(data.get("id") or "")
        if not task_id:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="invalid_response",
                error_message="方舟响应缺少任务 id",
                retryable=False,
                raw_metadata=data,
            )
        return ProviderResult(
            success=True,
            provider=self.name,
            model=locked_model,
            external_request_id=task_id,
            usage={"output_video_seconds": float(req.duration)},
            raw_metadata={
                "protocol": "ark_v3",
                "deployment": self.deployment_name,
                "resolution": payload.get("resolution") or self.resolution_for(req, locked_model),
            },
        )

    async def _task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/contents/generations/tasks/{task_id}", headers=self._headers()
            )
            response.raise_for_status()
            return response.json()

    def _task_failure(self, task_id: str, data: dict[str, Any]) -> ProviderResult:
        status = str(data.get("status") or "").lower()
        error = data.get("error") or {}
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        message = str(error.get("message") or "") if isinstance(error, dict) else str(error)
        return ProviderResult(
            success=False,
            provider=self.name,
            model=str(data.get("model") or self.model_name),
            external_request_id=task_id,
            error_code=code or f"task_{status or 'failed'}",
            error_message=(message or f"方舟任务状态: {status}")[:500],
            retryable=status in _RETRYABLE_TASK_STATUSES,
            raw_metadata=data,
        )

    async def get_status(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        try:
            data = await self._task(external_task_id)
        except httpx.HTTPError as error:
            return self._error(error, task_id=external_task_id)
        status = str(data.get("status") or "").lower()
        if status == "succeeded":
            return ProviderResult(
                success=True,
                provider=self.name,
                model=str(data.get("model") or self.model_name),
                external_request_id=external_task_id,
                raw_metadata=data,
            )
        if status in _PENDING_STATUSES:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=self.model_name,
                external_request_id=external_task_id,
                error_code="pending",
                error_message=f"方舟任务进行中: {status}",
                retryable=True,
                raw_metadata=data,
            )
        return self._task_failure(external_task_id, data)

    async def cancel(self, external_task_id: str) -> bool:
        """方舟只允许取消排队中的任务；运行中的任务会返回错误，视为未取消。"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.delete(
                    f"{self.base_url}/contents/generations/tasks/{external_task_id}", headers=self._headers()
                )
            return response.status_code in {200, 204}
        except httpx.HTTPError:
            return False

    def _validate_artifact_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise ValueError("方舟产物 URL 必须是 https")
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("方舟产物 URL 不能指向本机")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
            raise ValueError("方舟产物 URL 不能指向内网地址")
        if self.artifact_allowed_host_suffixes and not any(
            host == suffix or host.endswith(f".{suffix}") for suffix in self.artifact_allowed_host_suffixes
        ):
            raise ValueError("方舟产物 URL 主机不在允许列表")

    async def _download(self, client: httpx.AsyncClient, url: str, allowed_mime: set[str]) -> bytes:
        self._validate_artifact_url(url)
        chunks: list[bytes] = []
        size = 0
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if mime and mime not in allowed_mime:
                raise ValueError(f"方舟产物 MIME 不合法: {mime}")
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.max_artifact_bytes:
                    raise ValueError("方舟产物超过大小限制")
                chunks.append(chunk)
        if not size:
            raise ValueError("方舟产物为空")
        return b"".join(chunks)

    async def fetch_result(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        try:
            data = await self._task(external_task_id)
        except httpx.HTTPError as error:
            return self._error(error, task_id=external_task_id)
        status = str(data.get("status") or "").lower()
        if status != "succeeded":
            if status in _PENDING_STATUSES:
                return ProviderResult(
                    success=False,
                    provider=self.name,
                    model=self.model_name,
                    external_request_id=external_task_id,
                    error_code="pending",
                    error_message="方舟任务尚未完成",
                    retryable=True,
                )
            return self._task_failure(external_task_id, data)

        content = data.get("content") or {}
        video_url = str(content.get("video_url") or "")
        if not video_url:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=self.model_name,
                external_request_id=external_task_id,
                error_code="artifact_missing",
                error_message="方舟任务成功但没有 video_url（链接可能已过期）",
                retryable=False,
                raw_metadata=data,
            )
        try:
            async with httpx.AsyncClient(timeout=self.artifact_timeout, follow_redirects=False) as client:
                video = await self._download(client, video_url, _VIDEO_MIME)
                last_frame: Optional[bytes] = None
                last_frame_url = str(content.get("last_frame_url") or "")
                if last_frame_url:
                    try:
                        last_frame = await self._download(client, last_frame_url, _IMAGE_MIME)
                    except (httpx.HTTPError, ValueError):
                        last_frame = None  # 尾帧只用于镜头接力，下载失败不影响视频结果
        except (httpx.HTTPError, ValueError) as error:
            result = self._error(error, task_id=external_task_id)
            if isinstance(error, ValueError):
                result.error_code = "artifact_invalid"
                result.retryable = False
            return result

        suffix = uuid.uuid4().hex[:8]
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        video_name = f"ark_{external_task_id}_{suffix}.mp4"
        (VIDEOS_DIR / video_name).write_bytes(video)
        artifact: dict[str, Any] = {
            "type": "video",
            "storage_key": f"videos/{video_name}",
            "sha256": hashlib.sha256(video).hexdigest(),
        }
        duration = data.get("duration")
        if isinstance(duration, (int, float)):
            artifact["duration_ms"] = int(float(duration) * 1000)
        if last_frame is not None:
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            frame_name = f"ark_{external_task_id}_{suffix}_last.png"
            (IMAGES_DIR / frame_name).write_bytes(last_frame)
            artifact["last_frame_storage_key"] = f"images/{frame_name}"

        usage_raw = data.get("usage") or {}
        usage: dict[str, Any] = {}
        if isinstance(duration, (int, float)):
            usage["output_video_seconds"] = float(duration)
        for key in ("completion_tokens", "total_tokens"):
            if usage_raw.get(key) is not None:
                usage[key] = int(usage_raw[key])
        return ProviderResult(
            success=True,
            provider=self.name,
            model=str(data.get("model") or self.model_name),
            external_request_id=external_task_id,
            artifacts=[artifact],
            usage=usage,
            raw_metadata={
                "status": status,
                "resolution": data.get("resolution"),
                "seed": data.get("seed"),
                "protocol": "ark_v3",
            },
        )


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return f"{error.get('code', '')}: {error.get('message', '')}".strip(": ")
    return response.text
