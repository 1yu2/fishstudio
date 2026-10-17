"""实例隔离的火山图片 Driver Adapter。

API Key、Base URL 和模型名全部属于实例；Adapter 不修改 Tool 模块全局变量。
旧环境变量只通过 ``from_legacy_env`` 构造兼容实例。
"""

import asyncio
import logging
import os
from typing import Any

import requests

from app.providers.base import ProviderResult
from app.providers.image_base import ImageGenerationRequest, ImageProvider
from app.tools.volcano_image_generation import (
    download_and_save_image,
    parse_size,
    prepare_image_input,
)

logger = logging.getLogger(__name__)


class VolcanoImageProvider(ImageProvider):
    """火山 Seedream 图片生成/编辑；每个 Deployment 是独立实例。"""

    name = "volcano_image"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        deployment_name: str = "",
        timeout_seconds: float = 120,
        max_concurrency: int = 4,
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.model_name = model_name.strip()
        self.deployment_name = deployment_name.strip()
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    @classmethod
    def from_legacy_env(cls, purpose: str = "image_generation") -> "VolcanoImageProvider":
        """仅在数据库没有图片配置时使用的旧 env 兼容入口。"""
        generation_model = os.getenv("VOLCANO_IMAGE_MODEL", "seedream-4.5").strip()
        model_name = (
            os.getenv("VOLCANO_EDIT_MODEL", generation_model).strip()
            if purpose == "image_edit"
            else generation_model
        )
        return cls(
            api_key=os.getenv("VOLCANO_API_KEY", ""),
            base_url=os.getenv("VOLCANO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            model_name=model_name,
            deployment_name=f"legacy-volcano-{purpose}",
        )

    def is_available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model_name)

    def _generate_sync(self, req: ImageGenerationRequest) -> dict:
        """执行一次同步 API 请求并把远端图片保存到本地。"""
        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": req.prompt,
            "size": parse_size(req.aspect_ratio),
            "n": 1,
            "response_format": "url",
            "stream": False,
            "watermark": True,
        }
        if req.source_image:
            payload["image"] = prepare_image_input(req.source_image)

        response = requests.post(
            f"{self.base_url}/images/generations",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            operation = "editing" if req.source_image else "generating"
            raise RuntimeError(
                f"Error {operation} image: API调用失败: status={response.status_code}, body={response.text}"
            )

        data = response.json()
        image_urls: list[str] = []
        if isinstance(data.get("data"), list):
            image_urls = [item.get("url") for item in data["data"] if item.get("url")]
        elif isinstance(data.get("images"), list):
            image_urls = [item.get("url") for item in data["images"] if item.get("url")]
        elif data.get("url"):
            image_urls = [data["url"]]
        if not image_urls:
            raise RuntimeError("图片 API 响应中没有 image URL")

        local_path = download_and_save_image(image_urls[0], req.prompt)
        if local_path.startswith("http"):
            raise RuntimeError(f"图片下载失败，仍为远端 URL: {local_path}")
        result = {
            "image_url": local_path,
            "original_url": image_urls[0],
            "local_path": local_path,
            "prompt": req.prompt,
            "provider": self.name,
        }
        if req.source_image:
            result["source_image"] = req.source_image
        return result

    async def generate(self, req: ImageGenerationRequest) -> ProviderResult:
        model = self.model_name
        if not self.is_available():
            return ProviderResult(
                success=False,
                provider=self.name,
                model=model,
                error_code="no_provider",
                error_message="图片 Deployment 配置不完整",
            )
        loop = asyncio.get_running_loop()
        # R0.11：限制 candidate_count 1~4
        candidate_count = max(1, min(req.candidate_count, 4))

        async def _one(index: int) -> dict:
            async with self._semaphore:
                raw = await loop.run_in_executor(None, self._generate_sync, req)
            raw["_candidate_index"] = index
            return raw

        tasks = [_one(i) for i in range(candidate_count)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        artifacts: list[dict] = []
        success_count = 0
        failed_count = 0
        first_error = ""
        first_error_code = ""

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                failed_count += 1
                if not first_error:
                    first_error = str(r)[:500]
                    first_error_code = "generate_failed"
                continue
            local_path = r.get("local_path") or r.get("image_url") or ""
            storage_key = local_path.removeprefix("/storage/").lstrip("/")
            if not storage_key or local_path.startswith("http"):
                failed_count += 1
                if not first_error:
                    first_error = f"生成返回但产物缺失: {local_path}"
                    first_error_code = "generate_failed"
                continue
            artifacts.append(
                {
                    "storage_key": storage_key,
                    "type": "image",
                    "_candidate_index": i,
                }
            )
            success_count += 1

        if success_count == 0 and failed_count > 0:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=model,
                error_code=first_error_code or "generate_failed",
                error_message=first_error or "全部候选图片生成失败",
                retryable=True,
                raw_metadata={
                    "candidate_count": candidate_count,
                    "success_count": 0,
                    "failed_count": failed_count,
                },
            )

        return ProviderResult(
            success=True,
            provider=self.name,
            model=model,
            external_request_id="",
            artifacts=artifacts,
            usage={"output_image_count": success_count},
            raw_metadata={
                "edited": bool(req.source_image),
                "mocked": False,
                "candidate_count": candidate_count,
                "success_count": success_count,
                "failed_count": failed_count,
                "partial": success_count < candidate_count,
            },
        )
