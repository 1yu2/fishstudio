"""Provider-neutral Model Runtime Registry/Catalog/Resolver（首个 Chat 切片）。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCapability:
    modalities: frozenset[str] = frozenset({"chat"})
    supports_tools: bool = False
    supports_json_schema: bool = False
    supports_vision: bool = False
    supports_reasoning: bool = False
    supports_stream: bool = True
    context_window: int = 0
    max_concurrency: int = 0


@dataclass(frozen=True)
class ModelPolicy:
    purpose: str = "chat"
    modality: str = "chat"
    quality_tier: str = "balanced"
    required_capabilities: frozenset[str] = frozenset()
    allowed_deployments: tuple[str, ...] = ()
    explicit_deployment: Optional[str] = None
    allow_fallback: bool = True


@dataclass(frozen=True)
class ChatModelRequest:
    policy: ModelPolicy = field(default_factory=ModelPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelRequestContext:
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    workflow_step_id: Optional[str] = None
    job_id: Optional[str] = None
    creative_role: str = "chat"
    prompt_version: str = ""


@dataclass(frozen=True)
class RoutingSnapshot:
    deployment_name: str
    provider: str
    model: str
    capability_version: str = ""
    price_table_version: str = ""
    input_hash: str = ""
    fallback_allowed: bool = True


@dataclass(frozen=True)
class ModelInvocationResult:
    request_id: str
    snapshot: RoutingSnapshot
    response: Any
    invocation_id: Optional[str] = None


ChatFactory = Callable[[], BaseChatModel]


@dataclass(frozen=True)
class ChatDeployment:
    name: str
    provider: str
    model: str
    factory: ChatFactory
    capabilities: ModelCapability = field(default_factory=ModelCapability)
    enabled: bool = True
    quality_score: float = 0.0
    cost_score: float = 0.0
    latency_score: float = 0.0


def _has_capability(deployment: ChatDeployment, capability: str) -> bool:
    mapping = {
        "tools": deployment.capabilities.supports_tools,
        "json_schema": deployment.capabilities.supports_json_schema,
        "vision": deployment.capabilities.supports_vision,
        "reasoning": deployment.capabilities.supports_reasoning,
        "stream": deployment.capabilities.supports_stream,
    }
    return mapping.get(capability, capability in deployment.capabilities.modalities)


class ModelRuntime:
    """共享 Catalog 与路由控制面；具体协议由注册的 factory/Adapter 实现。"""

    def __init__(self, *, persist_invocations: bool = False) -> None:
        self._deployments: dict[str, ChatDeployment] = {}
        self.persist_invocations = persist_invocations

    def register_chat(self, deployment: ChatDeployment) -> None:
        if deployment.name in self._deployments:
            raise ValueError(f"chat deployment already registered: {deployment.name}")
        self._deployments[deployment.name] = deployment

    def upsert_chat(self, deployment: ChatDeployment) -> None:
        self._deployments[deployment.name] = deployment

    def replace_chat(self, deployments: list[ChatDeployment]) -> None:
        """用完整 Catalog 原子替换 Chat 目录；已创建的模型实例不受影响。"""
        self._deployments = {item.name: item for item in deployments}

    def list_chat(self) -> list[ChatDeployment]:
        return list(self._deployments.values())

    def resolve_chat(self, policy: ModelPolicy) -> ChatDeployment:
        candidates = [
            deployment
            for deployment in self._deployments.values()
            if deployment.enabled
            and policy.modality in deployment.capabilities.modalities
            and all(_has_capability(deployment, cap) for cap in policy.required_capabilities)
        ]
        if policy.allowed_deployments:
            allowed = set(policy.allowed_deployments)
            candidates = [item for item in candidates if item.name in allowed]
        if policy.explicit_deployment:
            candidates = [item for item in candidates if item.name == policy.explicit_deployment]
            if not candidates:
                raise ValueError(
                    f"explicit deployment unavailable or unsupported: {policy.explicit_deployment}"
                )
        if not candidates:
            available = ", ".join(sorted(self._deployments)) or "<empty>"
            raise RuntimeError(
                f"no chat deployment satisfies purpose={policy.purpose}, "
                f"capabilities={sorted(policy.required_capabilities)}; available={available}"
            )
        if policy.quality_tier == "quality":
            candidates.sort(key=lambda item: (-item.quality_score, item.latency_score, item.name))
        elif policy.quality_tier in {"cheap", "cheapest"}:
            candidates.sort(key=lambda item: (item.cost_score, -item.quality_score, item.name))
        elif policy.quality_tier == "fast":
            candidates.sort(key=lambda item: (item.latency_score, -item.quality_score, item.name))
        else:
            candidates.sort(
                key=lambda item: (
                    -(item.quality_score + item.cost_score + item.latency_score),
                    item.name,
                )
            )
        return candidates[0]

    def create_chat(self, request: ChatModelRequest) -> BaseChatModel:
        deployment = self.resolve_chat(request.policy)
        model = deployment.factory()
        logger.info(
            "model runtime resolved deployment=%s provider=%s model=%s purpose=%s",
            deployment.name,
            deployment.provider,
            deployment.model,
            request.policy.purpose,
        )
        return model

    def route_chat(
        self,
        request: ChatModelRequest,
        *,
        context: Optional[ModelRequestContext] = None,
        input_payload: Any = None,
    ) -> tuple[ChatDeployment, RoutingSnapshot]:
        """先路由并生成可持久化快照；调用方确认后再 invoke。"""
        deployment = self.resolve_chat(request.policy)
        raw = repr(input_payload).encode() if input_payload is not None else b""
        snapshot = RoutingSnapshot(
            deployment_name=deployment.name,
            provider=deployment.provider,
            model=deployment.model,
            input_hash=hashlib.sha256(raw).hexdigest() if raw else "",
            fallback_allowed=request.policy.allow_fallback,
        )
        return deployment, snapshot

    async def invoke_chat(
        self,
        messages: Any,
        request: ChatModelRequest,
        *,
        context: Optional[ModelRequestContext] = None,
    ) -> ModelInvocationResult:
        """统一 Chat 调用入口；把运行时上下文透传给 LangChain callback。"""
        context = context or ModelRequestContext()
        deployment, snapshot = self.route_chat(
            request,
            context=context,
            input_payload=messages,
        )
        model = deployment.factory()
        invocation_id = None
        started = time.monotonic()
        if self.persist_invocations:
            from app.services.model_invocation_service import begin_managed

            invocation_id = await begin_managed(
                request_id=context.request_id,
                purpose=request.policy.purpose,
                modality=request.policy.modality,
                deployment_name=deployment.name,
                provider=deployment.provider,
                model=deployment.model,
                routing_snapshot=asdict(snapshot),
                project_id=context.project_id,
                workflow_run_id=context.workflow_run_id,
                workflow_step_id=context.workflow_step_id,
                job_id=context.job_id,
            )
        metadata = {
            **request.metadata,
            "request_id": context.request_id,
            "project_id": context.project_id,
            "workflow_step_id": context.workflow_step_id,
            "job_id": context.job_id,
            "creative_role": context.creative_role,
            "prompt_version": context.prompt_version,
            "deployment_name": deployment.name,
            "invocation_id": invocation_id,
        }
        try:
            response = await model.ainvoke(messages, config={"metadata": metadata})
        except Exception as error:
            if self.persist_invocations:
                from app.services.model_invocation_service import finish_managed

                await finish_managed(
                    invocation_id,
                    status="failed",
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_code=type(error).__name__,
                    error=str(error),
                )
            raise
        if self.persist_invocations:
            from app.services.model_invocation_service import finish_managed

            await finish_managed(
                invocation_id,
                status="succeeded",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return ModelInvocationResult(
            request_id=context.request_id,
            snapshot=snapshot,
            response=response,
            invocation_id=invocation_id,
        )


def _openai_compatible_factory(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key_env: str,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    model_kwargs: Optional[dict[str, Any]] = None,
) -> ChatFactory:
    """通用 OpenAI-compatible Adapter；密钥只在创建时从环境读取。"""

    def create() -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"未配置 {api_key_env}，无法调用 {provider}/{model}")
        from app.services.llm_usage_service import LLMUsageCallback

        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            streaming=True,
            stream_usage=True,
            max_tokens=max_tokens,
            callbacks=[LLMUsageCallback(provider, model)],
            model_kwargs=model_kwargs or {"parallel_tool_calls": False},
        )

    return create


def _register_defaults(runtime: ModelRuntime) -> None:
    """注册兼容层默认端点；任意新 OpenAI-compatible 端点走配置注册。"""
    from app.llm.siliconflow import SiliconFlowLLMProvider
    from app.llm.volcano import VolcanoLLMProvider

    runtime.upsert_chat(
        ChatDeployment(
            name="volcano.default",
            provider="volcano",
            model=os.getenv("VOLCANO_MODEL_NAME", "doubao-seed-1-6-251015").strip(),
            factory=lambda: VolcanoLLMProvider().create_model(),
            capabilities=ModelCapability(
                supports_tools=True,
                supports_json_schema=True,
                supports_reasoning=True,
            ),
            quality_score=0.9,
            latency_score=0.7,
            cost_score=0.5,
        )
    )
    runtime.upsert_chat(
        ChatDeployment(
            name="siliconflow.default",
            provider="siliconflow",
            model=os.getenv("MODEL_NAME", "deepseek-ai/DeepSeek-V3.1-Terminus").strip(),
            factory=lambda: SiliconFlowLLMProvider().create_model(),
            capabilities=ModelCapability(
                supports_tools=True,
                supports_json_schema=True,
                supports_reasoning=True,
            ),
            quality_score=0.8,
            latency_score=0.8,
            cost_score=0.8,
        )
    )
    raw = os.getenv("MODEL_RUNTIME_DEPLOYMENTS_JSON", "").strip()
    if not raw:
        return
    try:
        configs = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("MODEL_RUNTIME_DEPLOYMENTS_JSON 不是合法 JSON") from error
    if not isinstance(configs, list):
        raise RuntimeError("MODEL_RUNTIME_DEPLOYMENTS_JSON 必须是数组")
    for config in configs:
        if not isinstance(config, dict):
            raise RuntimeError("MODEL_RUNTIME_DEPLOYMENTS_JSON 的每项必须是对象")
        name = str(config.get("name") or "").strip()
        provider = str(config.get("provider") or "openai_compatible").strip()
        model = str(config.get("model") or "").strip()
        base_url = str(config.get("base_url") or "").strip()
        api_key_env = str(config.get("api_key_env") or "OPENAI_API_KEY").strip()
        if not name or not model or not base_url:
            raise RuntimeError("Model Runtime Deployment 必须包含 name、model、base_url")
        capabilities = set(config.get("capabilities") or ["chat", "stream"])
        runtime.upsert_chat(
            ChatDeployment(
                name=name,
                provider=provider,
                model=model,
                factory=_openai_compatible_factory(
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    api_key_env=api_key_env,
                    temperature=float(config.get("temperature", 0.7)),
                    max_tokens=int(config.get("max_tokens", 2048)),
                    model_kwargs=config.get("model_kwargs"),
                ),
                capabilities=ModelCapability(
                    modalities=frozenset(capabilities),
                    supports_tools="tools" in capabilities,
                    supports_json_schema="json_schema" in capabilities,
                    supports_vision="vision" in capabilities,
                    supports_reasoning="reasoning" in capabilities,
                    supports_stream="stream" in capabilities,
                ),
                quality_score=float(config.get("quality_score", 0)),
                cost_score=float(config.get("cost_score", 0)),
                latency_score=float(config.get("latency_score", 0)),
            )
        )


_RUNTIME: Optional[ModelRuntime] = None


def get_model_runtime() -> ModelRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = ModelRuntime(persist_invocations=True)
        _register_defaults(_RUNTIME)
        _RUNTIME.catalog_revision = "legacy-env"
    from app.model_runtime.catalog import get_catalog

    snapshot = get_catalog().snapshot
    if snapshot.deployments and getattr(_RUNTIME, "catalog_revision", "") != snapshot.catalog_revision:
        deployments = [
            item.instance for item in snapshot.list("chat")
            if isinstance(item.instance, ChatDeployment) and item.enabled
        ]
        _RUNTIME.replace_chat(deployments)
        _RUNTIME.catalog_revision = snapshot.catalog_revision
    return _RUNTIME
