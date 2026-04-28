"""
火山引擎 LLM 实现
支持 thinking 功能（深度思考能力）
"""
import os
import logging
from typing import Optional
from langchain_openai import ChatOpenAI
from app.llm.base import BaseLLMProvider

logger = logging.getLogger(__name__)


class VolcanoLLMProvider(BaseLLMProvider):
    """火山引擎 LLM 提供商"""
    
    def __init__(self):
        # 从环境变量获取配置（与图片生成保持一致，使用 VOLCANO_ 前缀）
        self.api_key = os.getenv("VOLCANO_API_KEY", "").strip()
        self.base_url = os.getenv("VOLCANO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").strip()
        self.model_name = os.getenv("VOLCANO_MODEL_NAME", "doubao-seed-1-6-251015").strip()
        # thinking 功能配置
        thinking_enabled = os.getenv("VOLCANO_THINKING_ENABLED", "false").lower() == "true"
        self.thinking_type = "enabled" if thinking_enabled else "disabled"
        
        if not self.api_key:
            raise RuntimeError(
                "未配置 VOLCANO_API_KEY。请在 backend/.env 中设置，"
                "可参考 env.example（cp env.example .env）。"
            )
    
    def create_model(self) -> ChatOpenAI:
        """
        创建火山引擎 ChatModel 实例
        
        注意：火山引擎使用 OpenAI 兼容接口，支持 extra_body 参数配置 thinking
        LangChain 的 ChatOpenAI 底层使用 openai 库，extra_body 会通过 model_kwargs 传递
        """
        logger.info(f"🌋 创建火山引擎 LLM: model={self.model_name}, base_url={self.base_url}, thinking={self.thinking_type}")
        
        # 构建 model_kwargs，包含 thinking 配置
        # extra_body 会被传递给底层 OpenAI 客户端的 chat.completions.create() 方法
        # 测试验证：直接使用 OpenAI 客户端时 extra_body 可以正确传递
        # LangChain 的 ChatOpenAI 底层使用 openai 库，理论上也应支持
        model_kwargs = {
            "parallel_tool_calls": False,  # 禁止并行工具调用
            "extra_body": {
                "thinking": {
                    "type": self.thinking_type  # enabled 或 disabled
                }
            }
        }
        
        if self.thinking_type == "enabled":
            logger.info("💭 Thinking 功能已启用")
        
        # 创建 ChatOpenAI 实例（火山引擎兼容 OpenAI 接口）
        model = ChatOpenAI(
            model=self.model_name,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=0.7,
            streaming=True,  # 启用流式输出
            max_tokens=2048,
            model_kwargs=model_kwargs,
        )
        
        return model
    
    def get_provider_name(self) -> str:
        return "volcano"
