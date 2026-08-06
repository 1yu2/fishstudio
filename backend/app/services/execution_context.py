"""执行上下文：用 ContextVar 在请求/任务内传递 request_id / run_id 等关联 ID。"""

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExecutionContext:
    request_id: Optional[str] = None
    run_id: Optional[str] = None
    user_id: Optional[str] = None
    canvas_id: Optional[str] = None
    project_id: Optional[str] = None
    workflow_run_id: Optional[str] = None

    @classmethod
    def new_ids(cls, **kwargs) -> "ExecutionContext":
        """生成带新 request_id / run_id 的上下文，可用 kwargs 覆盖任意字段。"""
        base = {"request_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4())}
        base.update(kwargs)
        return cls(**base)


_context: ContextVar[ExecutionContext] = ContextVar(
    "execution_context", default=ExecutionContext()
)


def get_execution_context() -> ExecutionContext:
    return _context.get()


def set_execution_context(ctx: ExecutionContext) -> Token:
    return _context.set(ctx)


def reset_execution_context(token: Token) -> None:
    _context.reset(token)
