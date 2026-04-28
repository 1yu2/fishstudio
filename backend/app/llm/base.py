"""
LLM 基础接口
定义统一的 LLM 接口，所有 LLM 实现必须继承此接口
"""
from abc import ABC, abstractmethod
from typing import Any, Optional
from langchain_core.language_models import BaseChatModel


class BaseLLMProvider(ABC):
    """LLM 提供商基础类"""
    
    @abstractmethod
    def create_model(self) -> BaseChatModel:
        """
        创建 LangChain ChatModel 实例
        
        Returns:
            BaseChatModel: LangChain 兼容的聊天模型实例
        """
        pass
    
    @abstractmethod
    def get_provider_name(self) -> str:
        """
        获取提供商名称
        
        Returns:
            str: 提供商名称（如 "volcano", "siliconflow"）
        """
        pass
