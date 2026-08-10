# syntax=docker/dockerfile:1
# Tindalos 前端构建验证镜像（t8-web 验收 #1）：
#   容器内 npm ci（依 package-lock.json）+ vitest 全绿 + vite build（TS 严格）+ dist 存在。
# 构建上下文 = 仓库根：docker build -f .sandbox/web.Dockerfile -t tindalos-web:check .
# frontend/node_modules 由根 .dockerignore 排除（避免宿主依赖进上下文）。
FROM node:22-slim

WORKDIR /app

# 依赖层（利用构建缓存：仅锁文件变更才重装）
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error

# 源码层
COPY frontend/ ./

# vitest 全绿 → vite build（tsc 严格）→ 产物校验
RUN npm run test:ci -- --reporter=dot && npm run build && test -d dist && echo "=== web build OK ==="
