"""LLM Token 用量与人民币成本落账。

兼容 LangChain/OpenAI 风格的 usage_metadata、response_metadata 和 llm_output。
无法取得 Token 或价格时显式记录状态，绝不把未知费用静默写成 0 元。
"""

import os
import time
from decimal import Decimal
from typing import Any, Optional

from langchain_core.callbacks import AsyncCallbackHandler

from app.db.database import SessionLocal
from app.services import usage_service
from app.services.execution_context import get_execution_context


def _number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_usage(raw: dict[str, Any]) -> dict[str, int]:
    details = raw.get("input_token_details") or raw.get("prompt_tokens_details") or {}
    output_details = raw.get("output_token_details") or raw.get("completion_tokens_details") or {}
    return {
        "input_tokens": _number(raw.get("input_tokens", raw.get("prompt_tokens"))),
        "cached_input_tokens": _number(
            raw.get("cached_input_tokens", details.get("cache_read", details.get("cached_tokens")))
        ),
        "output_tokens": _number(raw.get("output_tokens", raw.get("completion_tokens"))),
        "reasoning_tokens": _number(
            raw.get(
                "reasoning_tokens", output_details.get("reasoning", output_details.get("reasoning_tokens"))
            )
        ),
    }


def extract_llm_usage(response: Any) -> Optional[dict[str, int]]:
    """从 LLMResult 或消息对象中提取统一 Token 字段。"""
    candidates: list[dict[str, Any]] = []
    llm_output = getattr(response, "llm_output", None) or {}
    if isinstance(llm_output, dict):
        for key in ("token_usage", "usage", "usage_metadata"):
            value = llm_output.get(key)
            if isinstance(value, dict):
                candidates.append(value)

    for generation_group in getattr(response, "generations", None) or []:
        for generation in generation_group or []:
            message = getattr(generation, "message", None)
            for value in (
                getattr(message, "usage_metadata", None),
                (getattr(message, "response_metadata", None) or {}).get("token_usage")
                if message is not None
                else None,
                (getattr(message, "response_metadata", None) or {}).get("usage")
                if message is not None
                else None,
            ):
                if isinstance(value, dict):
                    candidates.append(value)

    direct = getattr(response, "usage_metadata", None)
    if isinstance(direct, dict):
        candidates.append(direct)

    for candidate in candidates:
        usage = _normalize_usage(candidate)
        if usage["input_tokens"] or usage["output_tokens"]:
            return usage
    return None


def _price(name: str) -> Optional[Decimal]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except Exception:
        return None
    return value if value >= 0 else None


def calculate_llm_cost(usage: dict[str, int]) -> tuple[Optional[Decimal], dict[str, Any]]:
    """按每百万 Token 单价计算 CNY；任一必需价格缺失则不估算。"""
    input_price = _price("LLM_INPUT_PRICE_CNY_PER_MILLION")
    cached_price = _price("LLM_CACHED_INPUT_PRICE_CNY_PER_MILLION")
    output_price = _price("LLM_OUTPUT_PRICE_CNY_PER_MILLION")
    snapshot = {
        "input_price_cny_per_million": str(input_price) if input_price is not None else None,
        "cached_input_price_cny_per_million": (str(cached_price) if cached_price is not None else None),
        "output_price_cny_per_million": str(output_price) if output_price is not None else None,
    }
    if input_price is None or output_price is None:
        return None, snapshot
    # 未配置缓存价时按普通输入价计费，避免缓存 Token 被漏算。
    effective_cached_price = cached_price if cached_price is not None else input_price
    uncached_input = max(0, usage["input_tokens"] - usage["cached_input_tokens"])
    total = (
        Decimal(uncached_input) * input_price
        + Decimal(usage["cached_input_tokens"]) * effective_cached_price
        + Decimal(usage["output_tokens"]) * output_price
    ) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001")), snapshot


class LLMUsageCallback(AsyncCallbackHandler):
    """每个 ChatModel 实例绑定一个 Callback，按 LangChain run_id 独立计量。"""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self._started: dict[str, tuple[float, dict[str, Any]]] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        ctx = get_execution_context()
        context_snapshot = {
            "request_id": ctx.request_id,
            "run_id": ctx.run_id,
            "user_id": ctx.user_id,
            "project_id": ctx.project_id,
            "workflow_run_id": ctx.workflow_run_id,
            "workflow_step_id": ctx.workflow_step_id,
            "creative_role": ctx.creative_role or "chat",
            "prompt_version": ctx.prompt_version or "",
            **(metadata or {}),
        }
        self._started[str(run_id)] = (time.monotonic(), context_snapshot)

    async def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        started_at, metadata = self._started.pop(str(run_id), (time.monotonic(), {"creative_role": "chat"}))
        usage = extract_llm_usage(response)
        usage_status = "recorded" if usage is not None else "usage_unavailable"
        usage = usage or {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
        }
        cost, price_snapshot = calculate_llm_cost(usage)
        if usage_status == "recorded" and cost is None:
            usage_status = "pricing_unavailable"
        event_metadata = {
            "creative_role": metadata.get("creative_role", "chat"),
            "workflow_run_id": metadata.get("workflow_run_id"),
            "workflow_step_id": metadata.get("workflow_step_id"),
            "prompt_version": metadata.get("prompt_version", ""),
            "input_tokens": usage["input_tokens"],
            "cached_input_tokens": usage["cached_input_tokens"],
            "output_tokens": usage["output_tokens"],
            "reasoning_tokens": usage["reasoning_tokens"],
            "usage_status": usage_status,
            "price_table_version": os.getenv("LLM_PRICE_TABLE_VERSION", "unconfigured"),
            "price_snapshot": price_snapshot,
        }
        async with SessionLocal() as session:
            await usage_service.record_usage(
                session,
                request_id=metadata.get("request_id") or "",
                run_id=metadata.get("run_id") or "",
                invocation_id=metadata.get("invocation_id"),
                user_id=int(metadata["user_id"]) if metadata.get("user_id") else None,
                project_id=metadata.get("project_id"),
                workflow_run_id=metadata.get("workflow_run_id"),
                workflow_step_id=metadata.get("workflow_step_id"),
                purpose=metadata.get("creative_role", "chat"),
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cached_tokens=usage["cached_input_tokens"],
                operation=f"llm.{metadata.get('creative_role', 'chat')}",
                provider=self.provider,
                model=self.model,
                input_units=usage["input_tokens"],
                output_units=usage["output_tokens"],
                unit_type="token",
                estimated_cost=cost,
                actual_cost=cost,
                cost_source="provider_response" if usage_status != "usage_unavailable" else "estimate",
                currency="CNY",
                latency_ms=int((time.monotonic() - started_at) * 1000),
                status="success" if usage_status != "usage_unavailable" else "usage_unavailable",
                event_metadata=event_metadata,
            )
            await session.commit()

    async def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        started_at, metadata = self._started.pop(str(run_id), (time.monotonic(), {"creative_role": "chat"}))
        async with SessionLocal() as session:
            await usage_service.record_usage(
                session,
                request_id=metadata.get("request_id") or "",
                run_id=metadata.get("run_id") or "",
                invocation_id=metadata.get("invocation_id"),
                user_id=int(metadata["user_id"]) if metadata.get("user_id") else None,
                project_id=metadata.get("project_id"),
                workflow_run_id=metadata.get("workflow_run_id"),
                workflow_step_id=metadata.get("workflow_step_id"),
                purpose=metadata.get("creative_role", "chat"),
                operation=f"llm.{metadata.get('creative_role', 'chat')}",
                provider=self.provider,
                model=self.model,
                input_units=0,
                output_units=0,
                unit_type="token",
                latency_ms=int((time.monotonic() - started_at) * 1000),
                status="failed",
                error_code=type(error).__name__,
                event_metadata={"usage_status": "request_failed", "error": str(error)[:500]},
            )
            await session.commit()
