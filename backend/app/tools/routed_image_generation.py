"""Provider-neutral Agent 图片工具；配置只来自统一图片 Resolver。"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.providers.image_base import ImageGenerationRequest
from app.services import chat_generation_service as chat_jobs
from app.tools.chat_job_support import submit_and_wait, timeout_payload


class GenerateImageInput(BaseModel):
    prompt: str = Field(description="图像生成提示词，支持中英文")
    size: str = Field(default="1:1", description="输出图片宽高比或像素尺寸")


class EditImageInput(BaseModel):
    prompt: str = Field(description="图像编辑提示词，支持中英文")
    image_url: str = Field(description="需要编辑的本地图片路径")
    size: str = Field(default="1:1", description="输出图片宽高比或像素尺寸")


def _tool_payload(outcome: dict, *, edit: bool) -> str:
    """job 终态 → 工具返回；成功结构兼容旧版（image_url / local_path / message）并附 job_id。"""
    label = "编辑图片" if edit else "生成图片"
    if outcome["status"] == "timeout":
        return timeout_payload(outcome, "图片")
    if outcome["status"] != "succeeded" or not outcome.get("artifacts"):
        return f"Error {'editing' if edit else 'generating'} image: {outcome.get('error') or '没有产出图片'}"
    artifact = outcome["artifacts"][0]
    result = outcome.get("result") or {}
    return json.dumps(
        {
            "image_url": artifact["url"],
            "artifact_url": artifact["artifact_url"],
            "local_path": f"/storage/{artifact['storage_key']}",
            "job_id": outcome["job_id"],
            "artifact_id": artifact["artifact_id"],
            "provider": result.get("provider", ""),
            "model": result.get("model", ""),
            "mock": bool(result.get("mocked", False)),
            "message": f"图片已{label[:2]}并保存到本地",
        },
        ensure_ascii=False,
    )


async def _resolve_tool_image(session, req, purpose: str):
    """可测试的 Agent 入口；运行时延迟导入确保与 Worker 使用同一函数。"""
    from app.model_runtime.image import resolve_and_generate_image

    return await resolve_and_generate_image(session, req, purpose=purpose)


# 保留外部工具名，避免破坏已有 Prompt/Skill；生成改走 GenerationJob（和节点画布一致）：
# 受每日花费上限约束、可取消、留下 ModelInvocation / UsageEvent，由 Worker 执行。
@tool("generate_volcano_image", args_schema=GenerateImageInput)
async def generate_volcano_image_tool(prompt: str, size: str = "1:1") -> str:
    """按 image_generation Route 生成图片（异步任务，等待完成后返回）。"""
    try:
        outcome = await submit_and_wait(
            lambda session, **ctx: chat_jobs.submit_image_job(
                session, prompt=prompt, aspect_ratio=size, **ctx
            ),
            timeout=chat_jobs.IMAGE_TIMEOUT_SECONDS,
        )
        return _tool_payload(outcome, edit=False)
    except Exception as error:
        return f"Error generating image: {error}"


@tool("edit_volcano_image", args_schema=EditImageInput)
async def edit_volcano_image_tool(prompt: str, image_url: str, size: str = "1:1") -> str:
    """按 image_edit Route 编辑图片（异步任务，等待完成后返回）。"""
    try:
        outcome = await submit_and_wait(
            lambda session, **ctx: chat_jobs.submit_image_job(
                session, prompt=prompt, aspect_ratio=size, source_image=image_url, **ctx
            ),
            timeout=chat_jobs.IMAGE_TIMEOUT_SECONDS,
        )
        return _tool_payload(outcome, edit=True)
    except Exception as error:
        return f"Error editing image: {error}"
