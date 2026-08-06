"""火山引擎图片 provider：薄封装 app/tools/volcano_image_generation.py，不复制代码。

- text→image 走 generate_volcano_image_tool；image+prompt→image（首帧/角色参考图
  编辑）走 edit_volcano_image_tool。两个工具都是同步 requests 调用，这里用
  run_in_executor 避免阻塞事件循环（风格对齐 providers/tts_base.py）。
- 无 VOLCANO_API_KEY 时 is_available()=False（worker 侧再按 ALLOW_MOCK_PROVIDERS
  决定 mock 兜底或 no_provider 失败）。
"""

import asyncio
import json
import logging

from app.providers.base import ProviderResult
from app.providers.image_base import ImageGenerationRequest, ImageProvider
from app.tools import volcano_image_generation as vig

logger = logging.getLogger(__name__)

# Provider 内部并发上限（R0.11：防止无界 create_task 导致瞬时大并发调用）
_IMAGE_CONCURRENCY = 3
_image_semaphore = asyncio.Semaphore(_IMAGE_CONCURRENCY)


class VolcanoImageProvider(ImageProvider):
    """火山 Seedream 图片生成/编辑。工具落盘到 backend/storage/images/。"""

    name = "volcano_image"

    def is_available(self) -> bool:
        return bool(vig.VOLCANO_API_KEY)

    def _generate_sync(self, req: ImageGenerationRequest) -> dict:
        """同步调用火山图片工具，返回解析后的结果 dict（含 local_path）。"""
        if req.source_image:
            tool_fn = getattr(vig.edit_volcano_image_tool, "func", None)
            if tool_fn is None:
                raw = vig.edit_volcano_image_tool.invoke(
                    {
                        "prompt": req.prompt,
                        "image_url": req.source_image,
                        "size": req.aspect_ratio,
                    }
                )
            else:
                raw = tool_fn(req.prompt, req.source_image, req.aspect_ratio)
        else:
            tool_fn = getattr(vig.generate_volcano_image_tool, "func", None)
            if tool_fn is None:
                raw = vig.generate_volcano_image_tool.invoke(
                    {"prompt": req.prompt, "size": req.aspect_ratio}
                )
            else:
                raw = tool_fn(req.prompt, req.aspect_ratio)
        if isinstance(raw, str) and raw.startswith("Error"):
            raise RuntimeError(raw)
        return json.loads(raw)

    async def generate(self, req: ImageGenerationRequest) -> ProviderResult:
        model = vig.VOLCANO_EDIT_MODEL if req.source_image else vig.VOLCANO_IMAGE_MODEL
        if not self.is_available():
            return ProviderResult(
                success=False, provider=self.name, model=model,
                error_code="no_provider", error_message="未配置 VOLCANO_API_KEY",
            )
        loop = asyncio.get_running_loop()
        # R0.11：限制 candidate_count 1~4
        candidate_count = max(1, min(req.candidate_count, 4))

        async def _one(index: int) -> dict:
            async with _image_semaphore:
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
            artifacts.append({
                "storage_key": storage_key,
                "type": "image",
                "_candidate_index": i,
            })
            success_count += 1

        if success_count == 0 and failed_count > 0:
            return ProviderResult(
                success=False, provider=self.name, model=model,
                error_code=first_error_code or "generate_failed",
                error_message=first_error or "全部候选图片生成失败",
                retryable=True, raw_metadata={
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
