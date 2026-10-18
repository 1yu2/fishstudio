"""Provider-neutral Agent 图片工具；配置只来自统一图片 Resolver。"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.db.database import SessionLocal
from app.providers.image_base import ImageGenerationRequest


class GenerateImageInput(BaseModel):
    prompt: str = Field(description="图像生成提示词，支持中英文")
    size: str = Field(default="1:1", description="输出图片宽高比或像素尺寸")


class EditImageInput(BaseModel):
    prompt: str = Field(description="图像编辑提示词，支持中英文")
    image_url: str = Field(description="需要编辑的本地图片路径")
    size: str = Field(default="1:1", description="输出图片宽高比或像素尺寸")


def _tool_payload(execution) -> str:
    result = execution.result
    if not result.success:
        return f"Error generating image: {result.error_message or result.error_code}"
    artifact = result.artifacts[0] if result.artifacts else {}
    storage_key = artifact.get("storage_key", "")
    image_url = f"/storage/{storage_key}" if storage_key else ""
    return json.dumps(
        {
            "image_url": image_url,
            "local_path": image_url,
            "provider": result.provider,
            "model": result.model,
            "deployment": execution.deployment_name,
            "mock": execution.mocked,
            "message": "图片已生成并保存到本地",
        },
        ensure_ascii=False,
    )


async def _resolve_tool_image(session, req, purpose: str):
    """可测试的 Agent 入口；运行时延迟导入确保与 Worker 使用同一函数。"""
    from app.model_runtime.image import resolve_and_generate_image

    return await resolve_and_generate_image(session, req, purpose=purpose)


# 保留外部工具名，避免破坏已有 Prompt/Skill；实现已经不绑定 Volcano。
@tool("generate_volcano_image", args_schema=GenerateImageInput)
async def generate_volcano_image_tool(prompt: str, size: str = "1:1") -> str:
    """按 image_generation Route 生成图片。"""
    try:
        async with SessionLocal() as session:
            execution = await _resolve_tool_image(
                session,
                ImageGenerationRequest(prompt=prompt, aspect_ratio=size),
                purpose="image_generation",
            )
        return _tool_payload(execution)
    except Exception as error:
        return f"Error generating image: {error}"


@tool("edit_volcano_image", args_schema=EditImageInput)
async def edit_volcano_image_tool(prompt: str, image_url: str, size: str = "1:1") -> str:
    """按 image_edit Route 编辑图片。"""
    try:
        async with SessionLocal() as session:
            execution = await _resolve_tool_image(
                session,
                ImageGenerationRequest(prompt=prompt, source_image=image_url, aspect_ratio=size),
                purpose="image_edit",
            )
        return _tool_payload(execution)
    except Exception as error:
        return f"Error editing image: {error}"
