#!/usr/bin/env bash
# ============================================================
# GitHub 反向代理 v2.0 - 启动脚本
# 功能：设置环境变量并启动 Python 代理服务
# 用法：./start.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

# ---------- 端口配置 ----------
PROXY_PORT="${PROXY_PORT:-8081}"
export PROXY_PORT
export PROXY_HOST="${PROXY_HOST:-0.0.0.0}"

# ---------- 上游代理自动检测 ----------
# 若当前环境需要走 HTTP 代理才能访问 GitHub，
# 会自动用 https_proxy / HTTPS_PROXY 作为上游代理。
if [ -z "${UPSTREAM_PROXY:-}" ]; then
  UPSTREAM_PROXY="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}"
fi
export UPSTREAM_PROXY="${UPSTREAM_PROXY}"

# ---------- 自身对外地址（用于 URL 重写） ----------
export SELF_SCHEME="${SELF_SCHEME:-http}"
export SELF_HOST="${SELF_HOST:-localhost:${PROXY_PORT}}"

# ---------- 缓存配置 ----------
export CACHE_DISABLE="${CACHE_DISABLE:-}"
export CACHE_MAX="${CACHE_MAX:-2000}"
export CACHE_TTL="${CACHE_TTL:-43200}"

# ---------- 超时与重试 ----------
export TIMEOUT="${TIMEOUT:-60}"
export RETRY_TIMES="${RETRY_TIMES:-3}"

# ---------- 查找 Python 解释器 ----------
PYBIN="${PYTHON:-python3}"
if ! command -v "$PYBIN" >/dev/null 2>&1; then
  if command -v python >/dev/null 2>&1; then
    PYBIN="python"
  else
    echo "[start] ❌ 未找到 python3，请先运行 ./deploy.sh 安装依赖" >&2
    exit 1
  fi
fi

# ---------- 启动信息 ----------
echo "[start] 🚀 GitHub 反向代理 v2.0"
echo "[start] 📡 监听: http://${PROXY_HOST}:${PROXY_PORT}"
echo "[start] 🌐 访问: http://localhost:${PROXY_PORT}"
echo "[start] 🔄 上游代理: ${UPSTREAM_PROXY:-直连 (无代理)}"
echo "[start] 🔐 登录支持: 已启用"
echo "[start] 💾 资源缓存: $([ -n "$CACHE_DISABLE" ] && echo '关闭' || echo '开启')"
echo ""

# ---------- 启动服务 ----------
exec "$PYBIN" proxy.py
