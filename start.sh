#!/usr/bin/env bash
# 一键启动 GitHub 反向代理（监听 8081）
# 用法: ./start.sh
set -euo pipefail

cd "$(dirname "$0")"

# 端口（默认 8081，可改环境变量 PROXY_PORT）
PROXY_PORT="${PROXY_PORT:-8081}"
export PROXY_PORT
export PROXY_HOST="${PROXY_HOST:-0.0.0.0}"

# 自动检测上游 egress 代理：若当前环境需要走 http 代理才能访问 github，
# 会自动用 https_proxy / HTTPS_PROXY 作为上游代理（沙箱等环境）。
if [ -z "${UPSTREAM_PROXY:-}" ]; then
  UPSTREAM_PROXY="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}"
fi
export UPSTREAM_PROXY="${UPSTREAM_PROXY}"

# 自身对外地址（用于 URL 重写）；默认根据端口生成
export SELF_SCHEME="${SELF_SCHEME:-http}"
export SELF_HOST="${SELF_HOST:-localhost:${PROXY_PORT}}"

PYBIN="${PYTHON:-python3}"
if ! command -v "$PYBIN" >/dev/null 2>&1; then
  echo "[start] 未找到 python3，请先运行 ./deploy.sh 安装依赖或安装 python3" >&2
  exit 1
fi

echo "[start] listening on http://0.0.0.0:${PROXY_PORT}  (visit http://localhost:${PROXY_PORT})"
echo "[start] upstream proxy: ${UPSTREAM_PROXY:-direct (no proxy)}"
exec "$PYBIN" proxy.py
