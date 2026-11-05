"""兼容入口：通过 Model Runtime 解析 Chat Deployment。"""

import os
from typing import Optional

from langchain_core.language_models import BaseChatModel

from app.model_runtime import ChatModelRequest, ModelPolicy, get_model_runtime


def create_llm(
    provider: Optional[str] = None,
    *,
    purpose: str = "chat",
    quality_tier: str = "balanced",
    required_capabilities: frozenset[str] = frozenset({"stream"}),
) -> BaseChatModel:
    """
    创建 LLM 实例

    Args:
    provider: 兼容旧调用方的 provider 名称；实际解析由 Model Runtime 完成

    Returns:
        BaseChatModel: LangChain 兼容的聊天模型实例

    Raises:
        ValueError: 如果提供商名称不支持
        RuntimeError: 如果配置缺失
    """
    selected = (provider or os.getenv("LLM_PROVIDER", "volcano")).lower().strip()
    runtime = get_model_runtime()
    if provider is not None:
        deployment_name = f"{selected}.default"
    else:
        deployment_name = os.getenv("LLM_DEPLOYMENT_NAME", "").strip()
        if not deployment_name:
            # 旧的环境变量目录里才有 "<provider>.default"；数据库模型目录按 slug 命名（如 volcano-chat），
            # 这时不锁定名称，按用途（设置页「默认模型」）和能力路由，否则会报“部署不可用”。
            legacy = f"{selected}.default"
            if any(item.name == legacy for item in runtime.list_chat()):
                deployment_name = legacy
    return runtime.create_chat(
        ChatModelRequest(
            policy=ModelPolicy(
                purpose=purpose,
                modality="chat",
                quality_tier=quality_tier,
                required_capabilities=required_capabilities,
                explicit_deployment=deployment_name or None,
            )
        )
    )
