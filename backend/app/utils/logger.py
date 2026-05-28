"""日志配置：stdlib logging + structlog 双模式。

- LOG_FORMAT=console（默认，dev 友好）：彩色 + 时间戳 + 模块名
- LOG_FORMAT=json（生产）：每行一个 JSON 事件，可被 ELK/Loki/Datadog 采集

文件输出仍按日期 + 大小轮转保留（沿用原行为）。
"""

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

import structlog


def setup_logging(log_level: str = "INFO", log_dir: str | None = None, log_format: str = "console") -> None:
    """配置 stdlib logging + structlog。

    所有库（langchain / sqlalchemy / fastapi 等）的日志最终也会经过 structlog 渲染。
    """
    if log_dir is None:
        BASE_DIR = Path(__file__).parent.parent.parent
        log_dir = BASE_DIR / "logs"
    else:
        log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    log_file = log_dir / f"fishstudio_{today}.log"
    error_log_file = log_dir / f"fishstudio_error_{today}.log"

    level = getattr(logging, log_level.upper(), logging.INFO)

    # ─── structlog 处理链 ───────────────────────────────────────────
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 控制台 / 文件渲染器
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(
            colors=True, exception_formatter=structlog.dev.plain_traceback
        )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # 文件总是用 JSON（方便后续采集），不论 LOG_FORMAT 设啥
    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )

    # ─── 处理器 ─────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console_h = logging.StreamHandler()
    console_h.setLevel(level)
    console_h.setFormatter(formatter)
    root.addHandler(console_h)

    file_h = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_h.setLevel(logging.DEBUG)
    file_h.setFormatter(file_formatter)
    root.addHandler(file_h)

    error_h = logging.handlers.RotatingFileHandler(
        error_log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    error_h.setLevel(logging.ERROR)
    error_h.setFormatter(file_formatter)
    root.addHandler(error_h)

    # 第三方库降噪
    for noisy in ("httpx", "httpcore", "urllib3", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(
        "logging configured",
        extra={"level": log_level, "format": log_format, "log_dir": str(log_dir)},
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
