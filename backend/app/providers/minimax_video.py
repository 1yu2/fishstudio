"""MiniMax 视频生成 provider（主路径：官方公开 V1 协议；H3 V2 为实验分支）。

官方公开文档核实结果（2026-08-04，FetchURL 实读，非转述）：
- 创建: POST https://api.minimaxi.com/v1/video_generation
  body: {model, prompt, first_frame_image?, duration?, resolution?}
  - model: MiniMax-Hailuo-2.3 / MiniMax-Hailuo-2.3-Fast / MiniMax-Hailuo-02
    （文生视频枚举无 Fast；图生视频枚举含 Fast）
  - first_frame_image: 公网 URL 或 base64 Data URL（图生视频必填）
  - duration: 6 或 10（秒）；resolution: 768P（默认）/1080P（Hailuo-02 另有 512P）
  - 响应: {task_id, base_resp: {status_code, status_msg}}
  来源: platform.minimaxi.com/docs/api-reference/video-generation-t2v / video-generation-i2v
- 查询: GET /v1/query/video_generation?task_id=...
  响应: {task_id, status: Preparing|Queueing|Processing|Success|Fail,
         file_id(成功时), video_width, video_height, base_resp}
  来源: platform.minimaxi.com/docs/api-reference/video-generation-query
- 下载: 成功后用 file_id 调 GET /v1/files/retrieve?file_id=...
  响应: {file: {download_url, filename, ...}, base_resp}（示例含 download_url，
  schema 未列出该字段，按示例读取）
  来源: platform.minimaxi.com/docs/api-reference/file-management-retrieve
- 业务错误码（base_resp.status_code，公开文档）:
  0 成功；1001 超时(可重试)；1002/1039 限流(可重试)；1004/2049 鉴权失败(不可重试)；
  1008 余额不足(不可重试)；1026/1027 内容审核拒绝(不可重试)；1013 内部错误(可重试)；
  2013 参数异常(不可重试)。

仍是假设的点（公开文档未覆盖，真实联调需确认）：
1. files/retrieve 响应示例含 download_url 但 schema 未列出，按示例读取；
2. V1 失败任务（status=Fail）的具体错误文案字段（按 base_resp.status_msg 读取）。

H3 V2 实验分支（MINIMAX_H3_EXPERIMENTAL=true 才启用，默认 false）：
- 复审 0.9 结论：H3 的 /v2/video_generation、/v2/query/video_generation/{task_id}
  协议在当前公开文档中无法核验（文档索引虽有 v2 条目，但请求/响应 Schema 未经
  官方文档确认 + 无真实联调记录），因此默认禁用：H3 模型不出现在候选
  （pick_model 不选 H3、supports 对 H3 请求返回 False）。
- 开启后恢复原 H3-first 行为（_build_h3_body / _build_h3_regeneration_body /
  /v2 查询与 content.url 直链下载保持不变）。
"""

import base64
import logging
import os
from pathlib import Path

import httpx

from app.providers.base import ProviderResult, VideoGenerationRequest, VideoProvider

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
VIDEOS_DIR = BASE_DIR / "storage" / "videos"

# 官方 Base URL（路径在请求时拼接）
DEFAULT_BASE_URL = "https://api.minimaxi.com"

# 官方 V1 约束（公开文档）：duration 仅 6/10 秒；resolution 768P/1080P（Hailuo-02 另有 512P）
_V1_DURATIONS = (6, 10)
_V1_MODES = {"text", "first_frame"}
_V1_RESOLUTIONS = {"512P", "720P", "768P", "1080P"}

# H3 官方约束（实验分支）：duration 4~15 整数秒
_H3_DURATIONS = range(4, 16)

# 官方公开业务错误码（base_resp.status_code）-> (retryable, error_code)
_V1_BIZ_ERRORS: dict[int, tuple[bool, str]] = {
    1001: (True, "timeout"),
    1002: (True, "rate_limited"),
    1039: (True, "rate_limited"),
    1004: (False, "auth_failed"),
    2049: (False, "auth_failed"),
    1008: (False, "insufficient_balance"),
    1026: (False, "content_rejected"),
    1027: (False, "content_rejected"),
    1013: (True, "server_error"),
    2013: (False, "invalid_request"),
}


def h3_experimental_enabled() -> bool:
    """读取 MINIMAX_H3_EXPERIMENTAL（运行时直读 env，便于测试 monkeypatch）。"""
    return os.getenv("MINIMAX_H3_EXPERIMENTAL", "false").strip().lower() in ("1", "true", "yes")


def _to_data_url(path: str) -> str:
    """storage_key / /storage/ 路径 / 本地路径 -> base64 data URL；已是 data/url 则原样返回。

    路径解析优先级：绝对或相对本地路径 > /storage/ 前缀 > storage_key
    （如 "images/xxx.png" -> BASE_DIR/storage/images/xxx.png，与 job_planner
    写入 params.first_frame 的 storage_key 约定一致）。
    """
    if path.startswith(("data:", "http://", "https://")):
        return path
    if path.startswith("/storage/"):
        file_path = BASE_DIR / path.lstrip("/")
    else:
        file_path = Path(path)
        if not file_path.is_file():
            file_path = BASE_DIR / "storage" / path
    data = file_path.read_bytes()
    ext = file_path.suffix.lower().lstrip(".") or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    media = "image" if ext in ("jpeg", "png", "webp", "bmp", "gif") else ext
    return f"data:{media}/{ext};base64,{base64.b64encode(data).decode('utf-8')}"


def _media_item(kind: str, path: str, role: str = "") -> dict:
    """H3 实验分支 content 媒体项：{type: image_url|video_url|audio_url, <type>: {url}, role?}。"""
    item_type = f"{kind}_url"
    item: dict = {"type": item_type, item_type: {"url": _to_data_url(path)}}
    if role:
        item["role"] = role
    return item


class MiniMaxVideoProvider(VideoProvider):
    """MiniMax 视频生成。无 MINIMAX_API_KEY 时 is_available()=False。

    默认（H3 实验关闭）：仅 Hailuo V1 系列模型（官方公开协议已核验）。
    MINIMAX_H3_EXPERIMENTAL=true：恢复 H3-first（H3 协议未经官方文档核验）。
    """

    name = "minimax"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = (api_key if api_key is not None else os.getenv("MINIMAX_API_KEY", "")).strip()
        self.base_url = (
            (base_url if base_url is not None else os.getenv("MINIMAX_BASE_URL", DEFAULT_BASE_URL))
            .strip()
            .rstrip("/")
        )
        self.h3_model = os.getenv("MINIMAX_H3_MODEL", "MiniMax-H3").strip()
        self.v1_model = os.getenv("MINIMAX_V1_MODEL", "MiniMax-Hailuo-2.3").strip()
        self.fast_model = os.getenv("MINIMAX_FAST_MODEL", "MiniMax-Hailuo-2.3-Fast").strip()

    def is_available(self) -> bool:
        return bool(self.api_key)

    # ── 模型/能力选择 ──

    def _v1_models(self) -> set[str]:
        return {self.v1_model, self.fast_model, "MiniMax-Hailuo-02"}

    def pick_model(self, req: VideoGenerationRequest) -> str:
        """preview + first_frame -> Fast（省钱预览）；其余 -> V1 标准模型。

        H3 实验开启时：非 preview 场景恢复选 H3（原 H3-first 行为）。
        """
        if req.quality == "preview" and req.mode == "first_frame":
            return self.fast_model
        if h3_experimental_enabled():
            return self.h3_model
        return self.v1_model

    def resolution_for(self, req: VideoGenerationRequest, model: str = "") -> str:
        if not model:
            model = self.pick_model(req)
        if model == self.fast_model:
            return "768P"  # Fast 固定 768P
        if model == self.h3_model:
            if req.mode == "regenerate":
                return "2K"  # H3 实验分支：再生成固定输出 2K
            return req.resolution or "768P"  # H3: 768P / 2K
        # V1：768P（默认）/1080P（Hailuo-02 另有 512P）
        return req.resolution if req.resolution in _V1_RESOLUTIONS else "768P"

    def supports(self, req: VideoGenerationRequest, model: str = "") -> bool:
        """按模型能力矩阵。

        R6.3：model 非空时仅检查该锁定模型的能力，不进行内部 pick；
        空时保持原有 pick_model 行为。
        H3 实验关闭（默认）：仅 V1 系列能力——mode ∈ {text, first_frame}，
        duration ∈ {6,10}，分辨率 V1_RESOLUTIONS。
        H3 实验开启：恢复原 H3 全 mode 4~15s 的能力矩阵。
        """
        if not self.is_available():
            return False
        m = model or self.pick_model(req)
        if m == self.h3_model:
            if not h3_experimental_enabled():
                return False
            return req.duration in _H3_DURATIONS
        if m not in self._v1_models():
            return False
        if req.mode not in _V1_MODES or req.duration not in _V1_DURATIONS:
            return False
        resolution = self.resolution_for(req, m)
        if resolution == "512P":
            return m == "MiniMax-Hailuo-02" and req.mode == "first_frame"
        return resolution in {"768P", "1080P"}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    # ── 错误分类 ──

    @staticmethod
    def _classify_http_error(status_code: int, body: str = "") -> tuple[bool, str]:
        """-> (retryable, error_code)。429/5xx 可重试；4xx 区分审核拒绝与参数错误。"""
        if status_code == 429:
            return True, "rate_limited"
        if status_code >= 500:
            return True, "server_error"
        lowered = body.lower()
        if any(k in lowered for k in ("audit", "moderation", "reject", "审核")):
            return False, "content_rejected"
        return False, "invalid_request"

    @staticmethod
    def _classify_biz_error(status_code: int, status_msg: str = "") -> tuple[bool, str]:
        """官方公开业务错误码 -> (retryable, error_code)；未知码按文案兜底。"""
        if status_code in _V1_BIZ_ERRORS:
            return _V1_BIZ_ERRORS[status_code]
        lowered = status_msg.lower()
        if any(k in lowered for k in ("audit", "moderation", "reject", "敏感", "审核")):
            return False, "content_rejected"
        return False, "server_error"

    def _usage_for(self, req: VideoGenerationRequest) -> dict:
        """按 S7 维度计算 usage。

        输入视频时长：req 未携带各参考视频时长元数据，无法折算，记 0
        （后续在 req 增加 reference_video_durations 后再计入）。
        输入音频同理记 0。
        """
        usage = {
            "output_video_seconds": float(req.duration),
            "input_video_seconds": 0.0,
            "input_image_count": len(req.reference_images) + (1 if req.first_frame else 0),
            "input_audio_seconds": 0.0,
        }
        if req.mode == "regenerate":
            usage["output_video_seconds"] = 0.0
            usage["regeneration_output_seconds"] = float(req.duration)
        return usage

    # ── 请求构造器：V1（官方公开协议）与 H3（实验分支）完全独立 ──

    def _build_v1_body(self, req: VideoGenerationRequest, model: str = "") -> dict:
        """官方 V1 请求体：{model, prompt, first_frame_image?, duration, resolution}。

        first_frame_image 支持公网 URL 或 base64 Data URL（图生视频必填）；
        V1 无 aspect_ratio 字段（画幅由模型/首帧决定）。
        """
        body: dict = {
            "model": model or self.pick_model(req),
            "prompt": req.prompt,
            "duration": req.duration,
            "resolution": self.resolution_for(req, model),
        }
        if req.first_frame:
            body["first_frame_image"] = _to_data_url(req.first_frame)
        return body

    def _build_h3_body(self, req: VideoGenerationRequest, model: str = "") -> dict:
        """H3 实验分支请求体（协议未经官方文档核验，默认禁用）。"""
        content: list[dict] = [{"type": "text", "text": req.prompt}]
        if req.first_frame:
            content.append(_media_item("image", req.first_frame, role="first_frame"))
        if req.last_frame:
            content.append(_media_item("image", req.last_frame, role="last_frame"))
        for img in req.reference_images:
            content.append(_media_item("image", img, role="reference_image"))
        for vid in req.reference_videos:
            content.append(_media_item("video", vid, role="reference_video"))
        for aud in req.reference_audios:
            content.append(_media_item("audio", aud, role="reference_audio"))
        body: dict = {
            "model": model or self.h3_model,
            "content": content,
            "duration": req.duration,
            "resolution": self.resolution_for(req, model),
        }
        if not req.first_frame:
            body["ratio"] = req.aspect_ratio
        return body

    def _build_h3_regeneration_body(self, req: VideoGenerationRequest, model: str = "") -> dict:
        """H3 实验分支再生成请求体（协议未经官方文档核验，默认禁用）。"""
        body = self._build_h3_body(req, model)
        if not req.source_video:
            raise ValueError("regenerate 模式缺少 source_video")
        body["content"].append(_media_item("video", req.source_video, role="base_video"))
        body["resolution"] = "2K"
        return body

    def _submit_endpoint_and_body(self, req: VideoGenerationRequest, model: str) -> tuple[str, dict, str]:
        """按模型/模式选端点与构造器，返回 (url, body, protocol)。
        model 为空时内部 pick；非空时使用锁定模型（R2.4）。"""
        if not model:
            model = self.pick_model(req)
        if model in self._v1_models():
            return f"{self.base_url}/v1/video_generation", self._build_v1_body(req, model), "v1"
        if req.mode == "regenerate":
            return (
                f"{self.base_url}/v2/video_regeneration",
                self._build_h3_regeneration_body(req, model),
                "h3",
            )
        return f"{self.base_url}/v2/video_generation", self._build_h3_body(req, model), "h3"

    # ── 生命周期 ──

    async def submit(self, req: VideoGenerationRequest, *, model: str | None = None) -> ProviderResult:
        if model:
            locked_model = model
        else:
            locked_model = self.pick_model(req)
        try:
            url, body, protocol = self._submit_endpoint_and_body(req, locked_model)
        except Exception as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="invalid_request",
                error_message=f"输入素材读取失败: {e}",
            )
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="timeout",
                error_message=str(e),
                retryable=True,
            )
        except httpx.HTTPError as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="server_error",
                error_message=str(e),
                retryable=True,
            )

        if resp.status_code != 200:
            retryable, code = self._classify_http_error(resp.status_code, resp.text)
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code=code,
                error_message=resp.text[:500],
                retryable=retryable,
                raw_metadata={"status_code": resp.status_code},
            )
        data = resp.json()
        biz_code = data.get("code")
        status_msg = data.get("message", "")
        if isinstance(data.get("base_resp"), dict):
            if biz_code is None:
                biz_code = data["base_resp"].get("status_code")
            status_msg = status_msg or data["base_resp"].get("status_msg", "")
        if biz_code not in (0, None):
            retryable, code = self._classify_biz_error(int(biz_code), str(status_msg))
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code=code,
                error_message=str(status_msg)[:500],
                retryable=retryable,
                raw_metadata=data,
            )
        task_id = data.get("task_id") or (data.get("task") or {}).get("id") or ""
        if not task_id:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=locked_model,
                error_code="server_error",
                error_message="响应中无 task_id",
                retryable=True,
                raw_metadata=data,
            )
        return ProviderResult(
            success=True,
            provider=self.name,
            model=locked_model,
            external_request_id=str(task_id),
            usage=self._usage_for(req),
            raw_metadata={
                "protocol": protocol,
                "resolution": self.resolution_for(req, locked_model),
                "submit_response": data,
            },
        )

    # ── 查询（V1: GET /v1/query/video_generation?task_id=；H3: /v2/query/.../{id}）──

    async def _query_task(
        self, client: httpx.AsyncClient, external_task_id: str, protocol: str
    ) -> httpx.Response:
        if protocol == "h3":
            return await client.get(
                f"{self.base_url}/v2/query/video_generation/{external_task_id}",
                headers=self._headers(),
            )
        return await client.get(
            f"{self.base_url}/v1/query/video_generation",
            params={"task_id": external_task_id},
            headers=self._headers(),
        )

    async def get_status(self, external_task_id: str, protocol: str = "v1") -> ProviderResult:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await self._query_task(client, external_task_id, protocol)
        except httpx.TimeoutException as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                external_request_id=external_task_id,
                error_code="timeout",
                error_message=str(e),
                retryable=True,
            )
        except httpx.HTTPError as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                external_request_id=external_task_id,
                error_code="server_error",
                error_message=str(e),
                retryable=True,
            )
        if resp.status_code != 200:
            retryable, code = self._classify_http_error(resp.status_code, resp.text)
            return ProviderResult(
                success=False,
                provider=self.name,
                external_request_id=external_task_id,
                error_code=code,
                error_message=resp.text[:500],
                retryable=retryable,
            )
        data = resp.json()
        base_resp = data.get("base_resp") or {}
        biz_code = base_resp.get("status_code")
        if biz_code not in (0, None):
            retryable, code = self._classify_biz_error(int(biz_code), str(base_resp.get("status_msg", "")))
            return ProviderResult(
                success=False,
                provider=self.name,
                external_request_id=external_task_id,
                error_code=code,
                error_message=str(base_resp.get("status_msg", ""))[:500],
                retryable=retryable,
                raw_metadata={"raw": data},
            )
        if protocol == "h3":
            task = data.get("task") if isinstance(data.get("task"), dict) else data
        else:
            task = data  # V1 响应顶层平铺
        status = str(task.get("status") or "").lower()
        if status in ("success", "succeeded", "finished", "done"):
            raw: dict = {"protocol": protocol, "raw": data}
            if protocol == "h3":
                raw["download_url"] = (task.get("content") or {}).get("url", "")
                raw["task_type"] = task.get("task_type", "generation")
                raw["usage"] = task.get("usage", {})
            else:
                # 官方 V1：成功返回 file_id，后续 files/retrieve 拿 download_url
                raw["file_id"] = str(task.get("file_id") or "")
                raw["video_width"] = task.get("video_width")
                raw["video_height"] = task.get("video_height")
            return ProviderResult(
                success=True,
                provider=self.name,
                external_request_id=external_task_id,
                raw_metadata=raw,
            )
        if status in ("fail", "failed", "error", "cancelled", "canceled"):
            err = task.get("error") or base_resp.get("status_msg") or data
            retryable, code = self._classify_http_error(400, str(err))
            return ProviderResult(
                success=False,
                provider=self.name,
                external_request_id=external_task_id,
                error_code=code if code == "content_rejected" else "task_failed",
                error_message=str(err)[:500],
                retryable=False if code == "content_rejected" else retryable,
                raw_metadata={"raw": data},
            )
        # Preparing / Queueing / Processing 等：进行中
        return ProviderResult(
            success=False,
            provider=self.name,
            external_request_id=external_task_id,
            error_code="pending",
            error_message=f"任务进行中: {status or 'unknown'}",
            retryable=True,
            raw_metadata={"raw": data},
        )

    async def _retrieve_download_url(self, client: httpx.AsyncClient, file_id: str) -> str:
        """官方 V1：GET /v1/files/retrieve?file_id=... -> file.download_url。"""
        resp = await client.get(
            f"{self.base_url}/v1/files/retrieve",
            params={"file_id": file_id},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") not in (0, None):
            raise RuntimeError(
                f"files/retrieve 业务错误: {base_resp.get('status_code')} {base_resp.get('status_msg', '')}"
            )
        return str((data.get("file") or {}).get("download_url") or "")

    async def fetch_result(self, external_task_id: str, protocol: str = "v1") -> ProviderResult:
        """下载产物落盘。

        V1（官方）：query 拿 file_id -> files/retrieve 拿 download_url -> 下载。
        H3（实验分支）：query 成功即返回 content.url 直链，无需 files/retrieve；
        再生成任务（task_type=regeneration）产物用独立文件名保存，禁止覆盖原产物。
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await self._query_task(client, external_task_id, protocol)
                resp.raise_for_status()
                data = resp.json()
                task = (
                    (data.get("task") if isinstance(data.get("task"), dict) else data)
                    if protocol == "h3"
                    else data
                )
                if protocol == "h3":
                    download_url = (task.get("content") or {}).get("url")
                else:
                    file_id = str(task.get("file_id") or "")
                    if not file_id:
                        return ProviderResult(
                            success=False,
                            provider=self.name,
                            external_request_id=external_task_id,
                            error_code="invalid_request",
                            error_message="任务完成但无 file_id",
                            raw_metadata={"raw": data},
                        )
                    download_url = await self._retrieve_download_url(client, file_id)
                if not download_url:
                    return ProviderResult(
                        success=False,
                        provider=self.name,
                        external_request_id=external_task_id,
                        error_code="invalid_request",
                        error_message="未能获取下载地址",
                        raw_metadata={"raw": data},
                    )
                async with httpx.AsyncClient(timeout=300) as dl_client:
                    vresp = await dl_client.get(download_url)
                    vresp.raise_for_status()
        except httpx.HTTPError as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                external_request_id=external_task_id,
                error_code="server_error",
                error_message=str(e),
                retryable=True,
            )
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        # regenerate 独立文件名，避免覆盖源视频产物
        prefix = "minimax_regen_" if task.get("task_type") == "regeneration" else "minimax_"
        filename = f"{prefix}{external_task_id}.mp4"
        (VIDEOS_DIR / filename).write_bytes(vresp.content)
        return ProviderResult(
            success=True,
            provider=self.name,
            external_request_id=external_task_id,
            artifacts=[{"storage_key": f"videos/{filename}", "type": "video"}],
            raw_metadata={
                "protocol": protocol,
                "download_url": download_url,
                "task_type": task.get("task_type", "generation") if protocol == "h3" else "generation",
            },
        )
