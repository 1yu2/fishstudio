"""可观测性：Sentry 接入 + request_id 上下文 + 统一异常响应格式。

- init_sentry()：SENTRY_DSN 为空时是 no-op；非空时挂 FastAPI 集成
- RequestIdMiddleware：每请求生成 request_id，绑定到 structlog contextvars，注入响应头
- install_exception_handlers(app)：所有异常归一为 {error_code, message, request_id} 结构
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.services.execution_context import (
    ExecutionContext,
    reset_execution_context,
    set_execution_context,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Sentry
# ─────────────────────────────────────────
def init_sentry() -> None:
    """SENTRY_DSN 非空时初始化 Sentry SDK。"""
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            send_default_pii=False,
            integrations=[
                FastApiIntegration(),
                StarletteIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
        )
        logger.info("Sentry initialized", extra={"environment": settings.environment})
    except Exception as e:
        logger.warning(f"Sentry init skipped: {type(e).__name__}: {e}")


# ─────────────────────────────────────────
# Request ID 中间件
# ─────────────────────────────────────────
class RequestIdMiddleware(BaseHTTPMiddleware):
    """每个请求绑一个 request_id 到 structlog contextvars + 响应头。

    上游已经带 X-Request-ID 时尊重，否则生成 uuid4。
    """

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=rid,
            method=request.method,
            path=request.url.path,
        )
        request.state.request_id = rid
        # 同步写入 ExecutionContext（user_id 等由后续服务填充）
        token = set_execution_context(ExecutionContext.new_ids(request_id=rid))

        try:
            response = await call_next(request)
        finally:
            # 即使异常也保证清理
            reset_execution_context(token)

        response.headers["X-Request-ID"] = rid
        return response


# ─────────────────────────────────────────
# 统一异常响应
# ─────────────────────────────────────────
def _error_payload(
    code: str, message: str, request: Request, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """统一错误响应。

    保留 `detail` 字段（FastAPI 原生格式，已有客户端/测试依赖），
    并在旁边附 `error.{code, message, request_id}` 结构化字段供新客户端使用。
    """
    rid = getattr(request.state, "request_id", "")
    payload: dict[str, Any] = {
        "detail": message,  # backward compat: 老代码 / 测试 / 前端读这个
        "error": {
            "code": code,
            "message": message,
            "request_id": rid,
        },
    }
    if extra:
        payload["error"].update(extra)
    return payload


def install_exception_handlers(app: FastAPI) -> None:
    """所有异常路径归一为 {error: {code, message, request_id}} 结构。"""

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException):
        # FastAPI 内置 401/403/404/etc 都走这条
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "unprocessable_entity",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(code, str(exc.detail), request),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        # Pydantic 的原始 errors() 会带回请求 input，并可能在 ctx 中包含
        # ValueError 对象。前者可能泄露 API Key，后者无法被 JSON 序列化。
        safe_errors = [
            {
                "type": error.get("type", "value_error"),
                "loc": error.get("loc", ()),
                "msg": error.get("msg", "Invalid value"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_payload(
                "validation_error",
                "Request payload validation failed",
                request,
                extra={"errors": safe_errors},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception):
        # 真 500：写日志（含 request_id），让 Sentry 兜住
        logger.exception(
            "unhandled exception",
            extra={"exc_type": type(exc).__name__, "path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload("internal_error", "Internal server error", request),
        )
