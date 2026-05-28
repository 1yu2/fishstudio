#!/usr/bin/env bash
# 把远程 Postgres 的 fishstudio 库一键搬到本地 docker compose 的 postgres 容器。
#
# 用法：
#   ./scripts/migrate-from-remote.sh                 # 默认从 backend/.env 读远程凭据
#   REMOTE_HOST=x.x.x.x REMOTE_PASSWORD=xxx ./scripts/migrate-from-remote.sh
#
# 工作流：
#   1) 确认本地 PG 容器在跑（自动 docker compose up -d postgres + 等 healthy）
#   2) 容器内 pg_dump <远程> | psql <本地>（单 exec，避免宿主机 pipe）
#   3) 本地用 SELECT count(*) 验证表行数
#
# 安全：--clean --if-exists 让本地库被重置后导入；远程库不会被改。

set -euo pipefail

# ─── 解析远程凭据 ──────────────────────────────────────────────
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/backend/.env"
if [[ -z "${REMOTE_HOST:-}" || -z "${REMOTE_USER:-}" || -z "${REMOTE_PASSWORD:-}" || -z "${REMOTE_DB:-}" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ 未提供 REMOTE_* 环境变量，且找不到 $ENV_FILE" >&2
    exit 1
  fi
  # 从 backend/.env 的 DATABASE_URL 解析（postgresql+asyncpg://user:pass@host:port/db）
  DB_URL=$(grep -E "^DATABASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2-)
  if [[ -z "$DB_URL" ]]; then
    echo "❌ $ENV_FILE 里没有 DATABASE_URL" >&2
    exit 1
  fi
  # 用 python 拆 URL，避免 sed/regex 在密码含特殊字符时出错
  eval "$(python3 - <<PY
from urllib.parse import urlparse
u = urlparse("$DB_URL".replace("postgresql+asyncpg", "postgresql"))
print(f"REMOTE_USER='{u.username or ''}'")
print(f"REMOTE_PASSWORD='{u.password or ''}'")
print(f"REMOTE_HOST='{u.hostname or ''}'")
print(f"REMOTE_PORT='{u.port or 5432}'")
print(f"REMOTE_DB='{(u.path or '/').lstrip('/') or 'postgres'}'")
PY
)"
fi
REMOTE_PORT="${REMOTE_PORT:-5432}"

echo "📡 远程源: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}/${REMOTE_DB}"
echo "🐳 本地目标: docker compose 的 postgres 服务"
echo

# ─── 1) 保证本地 PG 在跑且 healthy ─────────────────────────────
cd "$(dirname "$0")/.."
PG_CONTAINER="fishstudio-postgres"

if ! docker compose ps --status running --services | grep -q '^postgres$'; then
  echo "▶️  本地 postgres 未启动，docker compose up -d postgres ..."
  docker compose up -d postgres
fi

echo "⏳ 等待 postgres healthy ..."
for i in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$PG_CONTAINER" 2>/dev/null || echo "starting")
  if [[ "$status" == "healthy" ]]; then
    echo "✅ postgres healthy（${i}s）"
    break
  fi
  if [[ $i -eq 30 ]]; then
    echo "❌ postgres 30s 内未达到 healthy，退出" >&2
    docker compose logs --tail=30 postgres
    exit 1
  fi
  sleep 1
done

# 本地凭据从 .env / 默认值
LOCAL_USER="${POSTGRES_USER:-postgres}"
LOCAL_DB="${POSTGRES_DB:-fishstudio}"

# ─── 2) 容器内一气呵成：pg_dump | psql ─────────────────────────
echo
echo "📦 开始迁移：pg_dump --clean --if-exists --no-owner --no-acl ..."
docker compose exec -T postgres bash -c "
  set -euo pipefail
  PGPASSWORD='${REMOTE_PASSWORD}' pg_dump \
    -h '${REMOTE_HOST}' -p '${REMOTE_PORT}' \
    -U '${REMOTE_USER}' -d '${REMOTE_DB}' \
    --clean --if-exists --no-owner --no-acl \
  | psql -U '${LOCAL_USER}' -d '${LOCAL_DB}' --quiet --set ON_ERROR_STOP=on
"

# ─── 3) 验证 ───────────────────────────────────────────────────
echo
echo "🔍 校验本地表行数："
docker compose exec -T postgres psql -U "$LOCAL_USER" -d "$LOCAL_DB" -t -A -F" | " <<'SQL'
SELECT 'users',       count(*) FROM users
UNION ALL SELECT 'canvases',    count(*) FROM canvases
UNION ALL SELECT 'settings_kv', count(*) FROM settings_kv
UNION ALL SELECT 'audit_logs',  count(*) FROM audit_logs
ORDER BY 1;
SQL

echo
echo "✅ 迁移完成。下一步：docker compose up -d backend frontend"
