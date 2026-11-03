"""Agent 视频工具：改走 GenerationJob（和节点画布一致），由 Worker 执行并异步等待结果。

工具名与参数保持不变（generate_volcano_video）；旧的同步直连实现留在
volcano_video_generation.py 里仅供 Provider 复用，不再被 Agent 调用。
"""

from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services import chat_generation_service as chat_jobs
from app.tools.chat_job_support import submit_and_wait, timeout_payload


class GenerateVolcanoVideoInput(BaseModel):
    """视频生成输入参数"""

    prompt: str = Field(description="视频生成的提示词，详细描述想要生成的视频内容，支持中英文")
    duration: Optional[int] = Field(default=None, description="视频时长（秒），可选，4-12秒，默认5秒")
    ratio: str = Field(
        default="16:9", description="视频宽高比，支持 16:9, 9:16, 1:1, 4:3, 3:4, 21:9 等，默认 16:9"
    )
    mode: str = Field(
        default="text",
        description="生成模式：'text'（文生视频）、'image'（图生视频-首帧）、'start_end'（首尾帧）",
    )
    image_url: Optional[str] = Field(
        default=None,
        description="图片URL或本地路径（图生视频-首帧模式使用，如 /storage/images/xxx.jpg）",
    )
    start_image_url: Optional[str] = Field(
        default=None,
        description="首帧图片URL或本地路径（首尾帧模式使用，如 /storage/images/xxx.jpg）",
    )
    end_image_url: Optional[str] = Field(
        default=None,
        description="尾帧图片URL或本地路径（首尾帧模式使用，如 /storage/images/xxx.jpg）",
    )


def _video_payload(outcome: dict, *, prompt: str, mode: str, duration: Optional[int], ratio: str) -> str:
    """job 终态 → 工具返回；成功结构兼容旧版（video_url / local_path / message）并附 job_id。"""
    if outcome["status"] == "timeout":
        return timeout_payload(outcome, "视频")
    if outcome["status"] != "succeeded" or not outcome.get("artifacts"):
        return json.dumps(
            {
                "error": f"生成视频时出错: {outcome.get('error') or '没有产出视频'}",
                "job_id": outcome["job_id"],
            },
            ensure_ascii=False,
        )
    artifact = outcome["artifacts"][0]
    result = outcome.get("result") or {}
    return json.dumps(
        {
            "video_url": artifact["url"],
            "artifact_url": artifact["artifact_url"],
            "local_path": f"/storage/{artifact['storage_key']}",
            "job_id": outcome["job_id"],
            "artifact_id": artifact["artifact_id"],
            "prompt": prompt,
            "mode": mode,
            "duration": duration,
            "ratio": ratio,
            "provider": result.get("provider", ""),
            "model": result.get("model", ""),
            "mock": bool(result.get("mocked", False)),
            "message": "视频已生成并保存到本地",
        },
        ensure_ascii=False,
    )


@tool("generate_volcano_video", args_schema=GenerateVolcanoVideoInput)
async def generate_volcano_video_tool(
    prompt: str,
    duration: Optional[int] = None,
    ratio: str = "16:9",
    mode: str = "text",
    image_url: Optional[str] = None,
    start_image_url: Optional[str] = None,
    end_image_url: Optional[str] = None,
) -> str:
    """
    AI 视频生成服务（异步任务，等待完成后返回；按视频模型路由选择厂商）。
    支持三种模式：
    1. 文生视频（mode='text'）：基于文本描述生成视频
    2. 图生视频-首帧（mode='image'）：基于首帧图片（image_url）生成视频
    3. 首尾帧视频（mode='start_end'）：基于首帧（start_image_url）和尾帧（end_image_url）生成视频

    Args:
        prompt: 视频生成的提示词
        duration: 视频时长（秒），默认 5 秒
        ratio: 视频宽高比，默认 16:9
        mode: 生成模式：text / image / start_end
        image_url: 首帧图片URL或本地路径（mode='image'）
        start_image_url: 首帧图片URL或本地路径（mode='start_end'）
        end_image_url: 尾帧图片URL或本地路径（mode='start_end'）

    Returns:
        生成结果 JSON（含 video_url、job_id）或错误信息；超时返回仍在进行的 job_id
    """
    if mode == "image":
        first, last = image_url or start_image_url, None
    elif mode == "start_end":
        first, last = start_image_url or image_url, end_image_url
    else:
        first, last = None, None
    try:
        outcome = await submit_and_wait(
            lambda session, **ctx: chat_jobs.submit_video_job(
                session,
                prompt=prompt,
                duration=duration,
                ratio=ratio,
                first_frame=first,
                last_frame=last,
                **ctx,
            ),
            timeout=chat_jobs.VIDEO_TIMEOUT_SECONDS,
        )
        return _video_payload(outcome, prompt=prompt, mode=mode, duration=duration, ratio=ratio)
    except Exception as error:
        return json.dumps({"error": f"生成视频时出错: {error}"}, ensure_ascii=False)
