#!/bin/sh
# NovaOS VPS stable deployment - one command
#
# Deploys github-proxy + cloudflared tunnel on a REAL server (VPS).
# On a VPS the processes live forever (systemd manages them), unlike
# the auto-recycling sandbox where nothing persists.
#
# Usage (on the VPS, as root or with sudo):
#   bash vps-deploy/install.sh <cloudflare-token>
#
# After install:
#   systemctl status novaos-proxy novaos-tunnel
#   edit /etc/systemd/system/novaos-proxy.service (SELF_HOST, port, proxy)
#   point your Cloudflare public hostname ingress at http://localhost:8081
set -eu

TOKEN="${1:-}"
[ -n "$TOKEN" ] || { echo "usage: bash vps-deploy/install.sh <cloudflare-token>"; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/github-proxy

echo "==> 1/4 install cloudflared (official apt repo)"
curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg | tee /usr/share/keyrings/cloudflare-public-v2.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq
apt-get install -y -qq cloudflared python3

echo "==> 2/4 copy app + save token"
mkdir -p "$APP"
cp "$SRC"/proxy.py "$SRC"/nginx.conf "$APP/"
chmod 600 "$APP/.cloudflared-token" 2>/dev/null || true
umask 177; printf '%s' "$TOKEN" > "$APP/.cloudflared-token"; chmod 600 "$APP/.cloudflared-token"

echo "==> 3/4 install systemd units"
cp "$SRC/vps-deploy/novaos-proxy.service"  /etc/systemd/system/
cp "$SRC/vps-deploy/novaos-tunnel.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable novaos-proxy novaos-tunnel
systemctl restart novaos-proxy novaos-tunnel

echo "==> 4/4 verify"
sleep 3
systemctl --no-pager --lines=0 status novaos-proxy  | head -3 || true
systemctl --no-pager --lines=0 status novaos-tunnel | head -3 || true
echo ""
echo "DONE. Local proxy:  http://localhost:8081/  (health: /__health__)"
echo "Set Cloudflare public hostname ingress -> http://localhost:8081"
echo "Tunnel logs: journalctl -u novaos-tunnel -f"
