#!/usr/bin/env bash
# 自动部署脚本：依赖检查 + （可选）自动安装 + 启动
# 用法: ./deploy.sh
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PROXY_PORT:-8081}"

echo "==================== GitHub 反向代理 - 自动部署 ===================="
echo

# ---------- 1. 依赖检查：python3 ----------
echo "[1/4] 检查 python3 ..."
if command -v python3 >/dev/null 2>&1; then
  PYVER=$(python3 --version 2>&1)
  echo "      OK: $PYVER  ($(command -v python3))"
else
  echo "      缺少 python3，尝试自动安装 ..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y >/dev/null 2>&1 || true
    sudo apt-get install -y python3 >/dev/null 2>&1 || { echo "      apt 安装 python3 失败" >&2; exit 1; }
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y python3 >/dev/null 2>&1 || { echo "      yum 安装 python3 失败" >&2; exit 1; }
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add --no-cache python3 >/dev/null 2>&1 || { echo "      apk 安装 python3 失败" >&2; exit 1; }
  else
    echo "      无法自动安装 python3（找不到 apt/yum/apk），请手动安装后重试" >&2
    exit 1
  fi
  command -v python3 >/dev/null 2>&1 || { echo "      python3 仍不可用" >&2; exit 1; }
  echo "      已安装 python3: $(python3 --version 2>&1)"
fi

# ---------- 2. 可选：nginx（仅提示，本方案默认用 Python） ----------
echo "[2/4] 检查 nginx (可选，本方案默认用 Python) ..."
if command -v nginx >/dev/null 2>&1; then
  echo "      nginx 已安装: $(nginx -v 2>&1)"
  echo "      若想用 nginx 方案，可参考 nginx.conf (listen $PORT)"
else
  echo "      未安装 nginx -> 使用 Python 方案 (无第三方依赖, 开箱即用)"
fi

# ---------- 3. 网络检查：尝试访问 github.com ----------
echo "[3/4] 检查与 github.com 的连通性 ..."
if curl -sI -m 12 https://github.com/ >/dev/null 2>&1; then
  echo "      OK: 可直接访问 github.com"
elif [ -n "${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}" ]; then
  echo "      直连失败，但检测到 https_proxy -> 将通过 egress 代理访问 GitHub"
else
  echo "      警告: 当前环境无法访问 github.com，服务可启动但页面可能打不开"
  echo "      （若你在需要走代理的网络里，请设置 https_proxy 环境变量后再运行）"
fi

# ---------- 4. 启动 ----------
echo "[4/4] 启动服务 (端口 $PORT) ..."
echo
chmod +x ./start.sh
exec ./start.sh
