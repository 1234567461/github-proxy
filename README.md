# GitHub 反向代理 v2.0（github-mirror）

把 `github.com` 及其全部相关子域反代到**本地 8081 端口**，访问 `http://localhost:8081` 即可直接打开 GitHub 完整页面。

**v2.0 新特性：支持 GitHub 登录！** 现在可以在代理中直接登录你的 GitHub 账号，访问私有仓库、管理 Issue、合并 PR 等。

---

## 功能特性

### 核心功能
- ✅ **单文件 Python 实现，零第三方依赖**（仅用标准库）
- ✅ **支持 GitHub 登录** - 账号密码登录、双因素认证、会话保持
- ✅ **覆盖 GitHub 全部子域** - 主站 + 静态资源 + raw + API + codeload + 头像等
- ✅ **路径前缀路由** - 子域走 `/__<域名简写>__/...` 前缀
- ✅ **响应体 URL 自动重写** - 所有链接都走代理，点击不跳出
- ✅ **全方法透传** - GET/POST/PUT/DELETE/PATCH/OPTIONS 全支持

### 可靠性
- ✅ **自动重试** - 上游瞬时断连 / 5xx 指数退避重试
- ✅ **静态资源缓存** - CSS/JS/字体/头像内存缓存，二次访问零延迟
- ✅ **健康检查端点** - `GET /__health__` 监控服务状态
- ✅ **崩溃保护** - 客户端断连不影响服务稳定运行

### 网络适配
- ✅ **支持上游代理** - 沙箱/内网环境自动通过 egress 代理访问 GitHub
- ✅ **Nginx 可选方案** - 高性能服务器可用 Nginx 替代 Python 方案

---

## 快速开始

### 一行命令启动

```bash
git clone https://github.com/1234567461/github-proxy.git
cd github-proxy
./deploy.sh
```

### 指定端口启动

```bash
./deploy.sh 9090
```

### 仅启动（依赖已就绪）

```bash
./start.sh
```

### 启动后访问

| 地址 | 说明 |
|---|---|
| `http://localhost:8081/` | GitHub 首页 |
| `http://localhost:8081/login` | 登录页面 |
| `http://localhost:8081/torvalds/linux` | 仓库页面示例 |
| `http://localhost:8081/__raw__/torvalds/linux/master/README.md` | raw 文件访问 |
| `http://localhost:8081/__health__` | 服务健康检查 |

---

## 登录使用说明

### 如何登录

1. 启动代理后，访问 `http://localhost:8081/login`
2. 输入你的 GitHub 用户名/邮箱和密码
3. 如果开启了双因素认证，输入验证码
4. 登录成功后即可正常使用所有 GitHub 功能

### 登录支持的原理

- **Cookie 重写**：自动去掉 GitHub 的 `Domain=.github.com` 属性，让 Cookie 落到本地代理域名
- **Secure 降级**：去掉 `Secure` 标记，允许 HTTP 下浏览器发送 Cookie
- **SameSite 调整**：设为 `Lax`，允许登录跳转时携带会话 Cookie
- **跳转重写**：登录流程中所有子域跳转（github.com → api.github.com 等）自动重写为代理路径

### 注意事项

> ⚠️ **安全提示**：本代理用于个人学习/开发用途。如果部署在公网服务器上，请务必：
> 1. 配置 HTTPS（通过 Nginx/Caddy 等前端反向代理）
> 2. 添加访问控制（IP 白名单 / Basic Auth）
> 3. 不要在公共网络环境下使用 HTTP 登录

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `proxy.py` | Python 反向代理主程序（核心，约 500 行） |
| `start.sh` | 启动脚本（设置环境变量并启动 Python 服务） |
| `deploy.sh` | 一键部署脚本（依赖检查 + 自动安装 + 启动） |
| `nginx.conf` | Nginx 反代配置（高性能方案，可选） |
| `README.md` | 本说明文档 |

---

## 环境变量配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PROXY_PORT` | `8081` | 监听端口 |
| `PROXY_HOST` | `0.0.0.0` | 监听地址 |
| `UPSTREAM_PROXY` | 自动检测 | 上游 egress 代理地址 |
| `SELF_SCHEME` | `http` | 重写 URL 的协议（HTTPS 部署时设为 `https`） |
| `SELF_HOST` | `localhost:端口` | 重写 URL 的 host |
| `PYTHON` | `python3` | Python 解释器路径 |
| `TIMEOUT` | `60` | 上游请求超时（秒） |
| `RETRY_TIMES` | `3` | 上游错误重试次数 |
| `CACHE_DISABLE` | 空 | 设为 `1` 关闭静态资源缓存 |
| `CACHE_MAX` | `2000` | 缓存条目上限 |
| `CACHE_TTL` | `43200` | 缓存有效期（秒，默认 12 小时） |

---

## 路由前缀说明

所有 GitHub 子域通过路径前缀访问：

| 前缀 | 对应域名 | 用途 |
|---|---|---|
| `/__assets__/` | `github.githubassets.com` | CSS/JS/字体/静态资源 |
| `/__raw__/` | `raw.githubusercontent.com` | 仓库原始文件 |
| `/__api__/` | `api.github.com` | GitHub API |
| `/__codeload__/` | `codeload.github.com` | 源码打包下载 |
| `/__avatars__/` | `avatars.githubusercontent.com` | 用户头像 |
| `/__objects__/` | `objects.githubusercontent.com` | Git 对象存储 |
| `/__userimages__/` | `user-images.githubusercontent.com` | 用户上传图片 |
| `/__releases__/` | `github-releases.githubusercontent.com` | Release 附件 |
| `/__gist__/` | `gist.github.com` | Gist 代码片段 |
| `/__docs__/` | `docs.github.com` | GitHub 文档 |
| `/__camo__/` | `camo.githubusercontent.com` | 图片代理缓存 |

---

## Nginx 方案（可选）

如果你的服务器能直连 GitHub，且追求更高性能，可以使用 Nginx 方案：

### 安装步骤

```bash
# 1. 安装 Nginx
sudo apt-get install -y nginx         # Debian/Ubuntu
# 或: sudo yum install -y nginx       # CentOS/RHEL

# 2. 复制配置
sudo cp nginx.conf /etc/nginx/conf.d/github-proxy.conf

# 3. 测试并重载
sudo nginx -t && sudo nginx -s reload

# 4. 验证
curl -I http://localhost:8081/
```

### 注意事项

- Nginx 方案需要 `ngx_http_sub_module`（默认编译包含）
- 服务器必须能直连 `github.com`
- 如果需要通过代理访问 GitHub，请使用 Python 方案

---

## 部署到服务器

### 方式一：直接运行（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/1234567461/github-proxy.git
cd github-proxy

# 2. 一键部署
./deploy.sh
```

### 方式二：后台运行

```bash
# 使用 nohup 后台运行
nohup ./start.sh > proxy.log 2>&1 &

# 查看日志
tail -f proxy.log

# 停止服务
pkill -f "python3 proxy.py"
```

### 方式三：Systemd 服务（生产环境）

创建 `/etc/systemd/system/github-proxy.service`：

```ini
[Unit]
Description=GitHub Reverse Proxy
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/github-proxy
ExecStart=/usr/bin/python3 proxy.py
Restart=always
RestartSec=5
Environment=PROXY_PORT=8081
Environment=PROXY_HOST=0.0.0.0

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable github-proxy
sudo systemctl start github-proxy
sudo systemctl status github-proxy
```

---

## HTTPS 部署（推荐用于公网）

如果部署在公网服务器上，强烈建议配置 HTTPS：

### 使用 Caddy（最简单）

```Caddyfile
your-domain.com {
    reverse_proxy localhost:8081
}
```

### 使用 Nginx + Let's Encrypt

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

启动前设置环境变量：

```bash
export SELF_SCHEME=https
export SELF_HOST=your-domain.com
./start.sh
```

---

## 常见问题

### Q: 页面能打开但样式错乱？

**A:** 可能是静态资源加载失败。检查：
1. 访问 `/__health__` 确认服务正常
2. 查看浏览器控制台是否有 404 的静态资源
3. 尝试清除缓存 `CACHE_DISABLE=1 ./start.sh`

### Q: 登录后又跳回未登录状态？

**A:** 这是 Cookie 问题。v2.0 已修复此问题，请确保使用最新版本。如果仍有问题：
1. 清除浏览器对该域名的所有 Cookie
2. 确保使用 HTTP 访问（或正确配置 HTTPS）
3. 检查浏览器是否拦截了第三方 Cookie

### Q: 访问私有仓库提示 404？

**A:** 请先登录。确认登录状态：
1. 访问 `http://localhost:8081/login`
2. 输入账号密码完成登录
3. 登录成功后再访问私有仓库

### Q: 如何通过上游代理访问 GitHub？

**A:** 设置环境变量：

```bash
export https_proxy=http://your-proxy:port
./deploy.sh
```

### Q: 如何修改监听端口？

**A:** 方式一：命令行参数
```bash
./deploy.sh 9090
```

方式二：环境变量
```bash
PROXY_PORT=9090 ./start.sh
```

---

## 技术架构

```
浏览器
  ↓ (HTTP)
本地代理 (proxy.py :8081)
  ↓ (HTTPS + Host 头重写)
GitHub 上游服务器
  ├── github.com (主站)
  ├── github.githubassets.com (静态资源)
  ├── raw.githubusercontent.com (原始文件)
  ├── api.github.com (API)
  └── ... 其他子域
```

### 请求处理流程

1. 接收客户端请求，解析路径前缀确定上游域名
2. 重写请求头（Host、UA、Cookie 透传）
3. 向上游 GitHub 发起请求（支持上游代理）
4. 读取上游响应，解压 gzip/deflate
5. 重写响应体中的所有 GitHub URL（HTML/JSON/JS）
6. 重写响应头（Location 跳转、Set-Cookie Domain）
7. 静态资源写入内存缓存
8. 返回给客户端

---

## 更新日志

### v2.0（当前版本）
- ✨ **新增：支持 GitHub 登录会话保持**
- ✨ 新增：更多子域路由（skills/education/shop 等）
- ✨ 改进：Cookie 重写逻辑优化
- ✨ 改进：部署脚本增加端口冲突检测
- ✨ 改进：启动脚本增加详细信息输出
- 📝 文档：完全重写 README，增加登录说明和部署指南

### v1.1
- ✨ 上游瞬时错误自动重试
- ✨ 静态资源内存缓存
- ✨ 健康检查端点
- 🐛 修复 BrokenPipe 崩溃问题

### v1.0
- 🎉 初始版本发布
- 🎉 基础反向代理功能
- 🎉 Nginx 配置文件

---

## 许可证

本项目仅供学习和个人使用，请遵守 GitHub 的服务条款。
