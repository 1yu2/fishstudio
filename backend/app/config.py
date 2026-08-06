"""集中化配置（pydantic-settings）。

所有环境变量通过 Settings 类型化加载，启动时校验缺失项。
新代码统一使用 get_settings()；现有 os.getenv 调用先保持，逐步迁移。
"""

from functools import lru_cache
from typing import List

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # 数据库
    database_url: str = Field(
        ...,
        description="Postgres 连接串，例如 postgresql+asyncpg://user:pass@host:5432/fishstudio",
    )

    # 认证
    jwt_secret: str = Field(..., min_length=16, description="JWT 签名密钥（>=16 字符）")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 天

    # Admin 初始账号（启动时 upsert）
    admin_username: str = "admin"
    admin_password: str = Field(..., min_length=8, description="Admin 初始密码（>=8 字符）")

    # CORS：原始逗号串；通过 cors_origins 属性拆成 list（避开 pydantic-settings 的 JSON 解析）
    cors_origins_raw: str = Field(default="http://localhost:3000", alias="cors_origins")

    @computed_field  # type: ignore[misc]
    @property
    def cors_origins(self) -> List[str]:
        return [s.strip() for s in self.cors_origins_raw.split(",") if s.strip()]

    # 日志
    log_level: str = "INFO"
    # 日志格式：console（开发友好彩色） / json（生产，被 ELK/Loki 采集）
    log_format: str = "console"

    # 运行环境标识（dev / staging / prod），影响 Sentry tag 与日志输出策略
    environment: str = "dev"

    # Sentry：留空则关闭。前后端各一个（前端通过 /api/config 暴露 public DSN 也行）
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0  # 0.0 关闭性能追踪；生产可设 0.1

    # 是否允许 Mock Provider 兜底（无真实 provider 时静默生成假产物）。
    # 生产必须保持 "false"；测试/本地演示显式设 "true"。
    allow_mock_providers: str = "false"


def mock_providers_enabled() -> bool:
    """读取 ALLOW_MOCK_PROVIDERS（运行时直读 env，便于测试 monkeypatch）。"""
    import os

    return os.getenv("ALLOW_MOCK_PROVIDERS", "false").strip().lower() in ("1", "true", "yes")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
