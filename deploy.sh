#!/usr/bin/env bash
# ============================================================
# GitHub 反向代理 v2.0 - 一键自动部署脚本
# 功能：依赖检查 + 自动安装 + 网络检测 + 启动服务
# 用法：./deploy.sh [端口号]
# 示例：./deploy.sh          # 默认 8081 端口
#       ./deploy.sh 9090     # 指定 9090 端口
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

# ---------- 参数解析 ----------
PORT="${1:-${PROXY_PORT:-8081}}"
export PROXY_PORT="$PORT"

echo "============================================================"
echo "  GitHub 反向代理 v2.0 - 自动部署"
echo "============================================================"
echo ""
echo "  监听端口 : $PORT"
echo "  访问地址 : http://localhost:$PORT"
echo ""

# ---------- 1. 依赖检查：python3 ----------
echo "[1/5] 检查运行环境 ..."

PYBIN=""
if command -v python3 >/dev/null 2>&1; then
  PYBIN="python3"
elif command -v python >/dev/null 2>&1 && python -c "import sys; sys.exit(0 if sys.version_info[0]>=3 else 1)" 2>/dev/null; then
  PYBIN="python"
fi

if [ -n "$PYBIN" ]; then
  PYVER=$($PYBIN --version 2>&1)
  echo "      ✅ $PYVER  ($(command -v $PYBIN))"
else
  echo "      ❌ 未找到 python3，尝试自动安装 ..."
  
  INSTALL_OK=0
  if command -v apt-get >/dev/null 2>&1; then
    echo "      使用 apt-get 安装 ..."
    sudo apt-get update -y >/dev/null 2>&1 || true
    sudo apt-get install -y python3 >/dev/null 2>&1 && INSTALL_OK=1
  elif command -v yum >/dev/null 2>&1; then
    echo "      使用 yum 安装 ..."
    sudo yum install -y python3 >/dev/null 2>&1 && INSTALL_OK=1
  elif command -v apk >/dev/null 2>&1; then
    echo "      使用 apk 安装 ..."
    sudo apk add --no-cache python3 >/dev/null 2>&1 && INSTALL_OK=1
  elif command -v brew >/dev/null 2>&1; then
    echo "      使用 brew 安装 ..."
    brew install python3 >/dev/null 2>&1 && INSTALL_OK=1
  fi
  
  if [ "$INSTALL_OK" -eq 1 ] && command -v python3 >/dev/null 2>&1; then
    PYBIN="python3"
    echo "      ✅ 已安装: $(python3 --version 2>&1)"
  else
    echo "      ❌ 自动安装失败，请手动安装 Python 3.6+"
    echo "         Ubuntu/Debian: sudo apt install python3"
    echo "         CentOS/RHEL:   sudo yum install python3"
    echo "         macOS:         brew install python3"
    exit 1
  fi
fi
export PYTHON="$PYBIN"

# ---------- 2. 检查端口占用 ----------
echo "[2/5] 检查端口 $PORT 占用情况 ..."
if command -v ss >/dev/null 2>&1; then
  if ss -tlnp | grep -q ":$PORT "; then
    echo "      ⚠️  端口 $PORT 已被占用"
    echo "         正在尝试停止旧进程 ..."
    OLDPID=$(ss -tlnp | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
    if [ -n "$OLDPID" ]; then
      kill "$OLDPID" 2>/dev/null || true
      sleep 1
    fi
  fi
fi
echo "      ✅ 端口 $PORT 可用"

# ---------- 3. 网络连通性检测 ----------
echo "[3/5] 检测 GitHub 连通性 ..."
DIRECT_OK=0
PROXY_DETECTED=""

# 先检测直连
if curl -sI -m 10 https://github.com/ >/dev/null 2>&1; then
  echo "      ✅ 可直连 github.com"
  DIRECT_OK=1
else
  echo "      ⚠️  直连 github.com 失败"
fi

# 检测环境变量中的代理
if [ -n "${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}" ]; then
  PROXY_DETECTED="${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}"
  echo "      ℹ️  检测到环境代理: $PROXY_DETECTED"
  export UPSTREAM_PROXY="$PROXY_DETECTED"
fi

# 如果既不能直连也没有代理
if [ "$DIRECT_OK" -eq 0 ] && [ -z "$PROXY_DETECTED" ]; then
  echo "      ⚠️  当前环境无法访问 github.com"
  echo "         服务仍会启动，但页面可能无法加载"
  echo "         如需通过代理访问，请设置环境变量："
  echo "         export https_proxy=http://your-proxy:port"
fi

# ---------- 4. 配置信息 ----------
echo "[4/5] 配置服务参数 ..."
export PROXY_HOST="${PROXY_HOST:-0.0.0.0}"
export SELF_SCHEME="${SELF_SCHEME:-http}"
export SELF_HOST="${SELF_HOST:-localhost:$PORT}"

echo "      监听地址 : $PROXY_HOST:$PORT"
echo "      对外地址 : $SELF_SCHEME://$SELF_HOST"
echo "      上游代理 : ${UPSTREAM_PROXY:-直连}"
echo "      登录支持 : 已启用"

# ---------- 5. 启动服务 ----------
echo "[5/5] 启动 GitHub 反向代理服务 ..."
echo ""
echo "============================================================"
echo "  启动成功后，请访问："
echo ""
echo "  主页:   http://localhost:$PORT/"
echo "  登录:   http://localhost:$PORT/login"
echo "  健康检查: http://localhost:$PORT/__health__"
echo ""
echo "  按 Ctrl+C 停止服务"
echo "============================================================"
echo ""

chmod +x ./start.sh
exec ./start.sh
