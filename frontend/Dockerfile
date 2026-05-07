# syntax=docker/dockerfile:1.6
# ─── Stage 1: build ──────────────────────────────────────────────────
FROM node:20-alpine AS build

WORKDIR /app

# 先装依赖（利用 layer cache）
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

# 复制源码并构建
COPY tsconfig*.json vite.config.ts index.html ./
COPY public/ ./public/
COPY src/ ./src/
RUN npm run build

# ─── Stage 2: serve ──────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

# 把构建产物放到 nginx 默认根目录
COPY --from=build /app/dist /usr/share/nginx/html

# 自定义反代配置（替换 nginx 默认 server 块）
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
