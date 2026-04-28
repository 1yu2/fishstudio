"""
LLM 工厂 - 根据配置创建对应的 LLM 实例
"""
import os
import logging
from typing import Optional
from langchain_core.language_models import BaseChatModel
from app.llm.base import BaseLLMProvider
from app.llm.volcano import VolcanoLLMProvider
from app.llm.siliconflow import SiliconFlowLLMProvider

logger = logging.getLogger(__name__)


def create_llm(provider: Optional[str] = None) -> BaseChatModel:
    """
    创建 LLM 实例
    
    Args:
        provider: LLM 提供商名称（"volcano" 或 "siliconflow"），如果为 None 则从环境变量读取
    
    Returns:
        BaseChatModel: LangChain 兼容的聊天模型实例
    
    Raises:
        ValueError: 如果提供商名称不支持
        RuntimeError: 如果配置缺失
    """
    # 如果没有指定 provider，从环境变量读取（默认 volcano）
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "volcano").lower().strip()
    
    logger.info(f"🏭 创建 LLM: provider={provider}")
    
    # 根据 provider 创建对应的实例
    provider_instance: BaseLLMProvider
    
    if provider == "volcano":
        provider_instance = VolcanoLLMProvider()
    elif provider == "siliconflow":
        provider_instance = SiliconFlowLLMProvider()
    else:
        raise ValueError(
            f"不支持的 LLM 提供商: {provider}。"
            f"支持的提供商: volcano, siliconflow。"
            f"请在 .env 中设置 LLM_PROVIDER=volcano 或 LLM_PROVIDER=siliconflow"
        )
    
    # 创建并返回模型实例
    model = provider_instance.create_model()
    logger.info(f"✅ LLM 创建成功: provider={provider_instance.get_provider_name()}")
    
    return model
