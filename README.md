# GitHub 反向代理（github-mirror）

把 `github.com` 及其相关子域（静态资源 / raw / api / codeload / avatars 等）反代到**本地 8081 端口**，访问 `http://localhost:8081` 即可直接打开 GitHub 完整页面（仓库浏览、文件查看、issue、PR、用户主页等），点击链接不会跳出代理。

## 特性

- **单文件 Python 实现，零第三方依赖**（标准库 `http.server` + `urllib`），开箱即用
- **覆盖 GitHub 多域名**：主站 + `github.githubassets.com`(CSS/JS/图片) + `raw.githubusercontent.com` + `api.github.com` + `codeload.github.com` + avatars/objects/user-images/releases 等
- **路径前缀路由**：子域走 `/__<域名简写>__/...`，例如 `/__assets__/assets/index.js` → `github.githubassets.com/assets/index.js`
- **响应体 URL 重写**：把 `https://github.com/...` 等绝对 URL 改写成走代理，点链接不跳出
- **响应头清洗**：去掉 CSP / HSTS / X-Frame-Options，避免浏览器拦截资源；重写 `Location` 跳转、`Set-Cookie` 域
- **POST / API 透传**：GET/HEAD/POST/PUT/DELETE/PATCH/OPTIONS 全方法透传，不阻断
- **支持 egress 代理**：沙箱等需要走 HTTP 代理才能访问外网的环境，自动用 `https_proxy` 作为上游
- **附 nginx.conf**：在能直连 GitHub 的服务器上也可用 nginx 方案（性能更好）

## 一行命令启动

```bash
cd github-proxy && ./deploy.sh
```

或仅启动（依赖已就绪时）：

```bash
cd github-proxy && ./start.sh
```

启动后访问：

```
http://localhost:8081/              # GitHub 首页
http://localhost:8081/torvalds/linux   # 仓库页
http://localhost:8081/__raw__/torvalds/linux/master/README.md   # raw 文件
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `proxy.py` | Python 反向代理主程序（核心） |
| `start.sh` | 一键启动脚本（设端口/上游代理环境变量并 exec python3） |
| `deploy.sh` | 自动部署：依赖检查(python3) + 可选自动安装 + 网络检查 + 启动 |
| `nginx.conf` | nginx 反代配置（在能直连 github 的服务器上使用，listen 8081） |
| `README.md` | 本说明 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PROXY_PORT` | `8081` | 监听端口 |
| `PROXY_HOST` | `0.0.0.0` | 监听地址 |
| `UPSTREAM_PROXY` | （自动取 `https_proxy`） | 上游 egress 代理，沙箱等环境用；为空则直连 |
| `SELF_SCHEME` | `http` | 重写 URL 的协议（挂在前端 https 反代后可设 `https`） |
| `SELF_HOST` | `localhost:<port>` | 重写 URL 的 host（默认根据端口生成） |
| `PYTHON` | `python3` | python 解释器 |
| `TIMEOUT` | `60` | 上游请求超时(秒) |

## 在能联网的 Linux 服务器上用 nginx 方案（可选）

```bash
# 安装 nginx（需 root）
sudo apt-get install -y nginx         # Debian/Ubuntu
# 或: sudo yum install -y nginx       # CentOS/RHEL

# 部署配置
sudo cp nginx.conf /etc/nginx/conf.d/github-proxy.conf
sudo nginx -t && sudo nginx -s reload

# 访问
curl -I http://localhost:8081/
```

> nginx 方案要求编译了 `ngx_http_sub_module`（默认有），且服务器能直连 `github.com`。
> 如果服务器需要走代理才能访问 GitHub，请用 Python 方案（`./deploy.sh`，会自动用 `https_proxy`）。

## 沙箱验证结果

在沙箱（无 nginx，通过 `http://127.0.0.1:18080` egress 代理访问 GitHub）实测：

- ✅ `curl -I http://localhost:8081/` → `200 OK`，返回 GitHub 首页 HTML（含 22 处 "github"）
- ✅ `curl -s http://localhost:8081/torvalds/linux` → 200，`<title>GitHub - torvalds/linux: Linux kernel source tree</title>`
- ✅ 静态资源 `http://localhost:8081/__assets__/assets/react-*.js` → 200，`Cache-Control: immutable`
- ✅ raw 文件 `http://localhost:8081/__raw__/torvalds/linux/master/README` → 200，返回 `Linux kernel ...`
- ✅ API `http://localhost:8081/__api__/repos/torvalds/linux` → 200，返回仓库 JSON
- ✅ codeload `http://localhost:8081/__codeload__/torvalds/linux/tar.gz/refs/heads/master` → 200，`content-disposition: attachment; filename=linux-master.tar.gz`
- ✅ URL 重写：首页已无未重写的 `https://github.com`，静态资源 URL 全部改写为 `localhost:8081/__assets__/...`

> 注：`/torvalds/linux/master/README.md` 会返回 404 —— 因为该仓库根目录只有 `README`（无扩展名），上游 `raw.githubusercontent.com` 对 `.md` 路径本身返回 404，代理如实透传，属正常行为。

## 局限

- **登录态不维护**：公开页面够用；登录/私有内容因 cookie 域不同无法保持会话（设计取舍）
- 大体积二进制下载（release zip 等）会读入内存，超大文件可能受限
- 子域路由用 `__前缀__`（双下划线），GitHub 无此真实路径，不会冲突
