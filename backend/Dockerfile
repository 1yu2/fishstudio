# syntax=docker/dockerfile:1.6
# ─── Stage 1: builder ────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 编译期工具：少数没有预编译 wheel 的包需要
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/app/.local/bin:${PATH}" \
    PYTHONPATH=/app

# 运行期系统依赖：opencv 需要 libgl/glib；moviepy 需要 ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash app

# 把 builder 装好的依赖拷到 runtime（保留 --user 安装路径）
COPY --from=builder --chown=app:app /root/.local /home/app/.local

WORKDIR /app
COPY --chown=app:app app/ ./app/
COPY --chown=app:app skills/ ./skills/

# 运行时挂卷：storage（产物 + 旧迁移备份）+ workspace（用户记忆 md）+ logs
RUN mkdir -p /app/storage /app/workspace /app/logs && chown -R app:app /app

USER app
EXPOSE 8000

# 用 /health 做活探针；依赖 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 生产：不开 --reload；workers 数量由运行时控制（compose 里通过 command 覆盖）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
