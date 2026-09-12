# GitHub reverse proxy (github-mirror)

Reverse-proxy `github.com` and all its related subdomains (static assets / raw / api / codeload / avatars, etc.) to a **local port (default 8081)**. Open `http://localhost:8081` and browse the full GitHub experience (repo browsing, file views, issues, PRs, profile pages) — links never leave the proxy.

## Features

- **Single-file Python, zero third-party deps** (stdlib `http.server` + `urllib`), runs as-is
- **Multi-domain coverage**: main site + `github.githubassets.com` (CSS/JS/images) + `raw.githubusercontent.com` + `api.github.com` + `codeload.github.com` + avatars/objects/user-images/releases
- **Path-prefix routing**: subdomains map to `/__<shortname>__/...`, e.g. `/__assets__/assets/index.js` → `github.githubassets.com/assets/index.js`
- **Response-body URL rewrite**: rewrites absolute `https://github.com/...` to go through the proxy
- **Header scrubbing**: strips CSP / HSTS / X-Frame-Options so the browser doesn't block assets; rewrites `Location` redirects and `Set-Cookie` domains
- **POST / API passthrough**: GET/HEAD/POST/PUT/DELETE/PATCH/OPTIONS all pass through
- **Egress proxy support**: sandboxed environments that need an HTTP proxy to reach the internet can set `UPSTREAM_PROXY` / `https_proxy`
- **nginx.conf included**: for servers that can reach GitHub directly (higher performance)
- **Reliability (v1.1)**:
  - **Auto-retry** (backoff) on transient upstream drops / 5xx — no more one-off 502
  - **In-memory cache for content-hashed assets** (CSS/JS/fonts/avatars): second load hits zero upstream, eliminating “styles flash on and off / page looks like bare HTML / icons misplaced”
  - **Broken-pipe safe**: client disconnects no longer crash worker threads
  - Rewrites escaped `https:\/\/...` URLs inside embedded JSON too
  - Health check endpoint `GET /__health__`

## One command to start

```bash
cd github-proxy && ./deploy.sh
```

Or just start (deps already present):

```bash
cd github-proxy && ./start.sh
```

Then open:

```
http://localhost:8081/                                      # GitHub home
http://localhost:8081/torvalds/linux                        # a repo page
http://localhost:8081/__raw__/torvalds/linux/master/README   # a raw file
```

## Files

| File | Purpose |
|---|---|
| `proxy.py` | the reverse proxy (core) |
| `start.sh` | one-key start (sets port/upstream env and execs python3) |
| `deploy.sh` | auto deploy: python3 check + optional install + connectivity check + start |
| `nginx.conf` | nginx config (for servers that can reach GitHub directly, listen 8081) |
| `README.md` | Chinese doc |
| `README.en.md` | this file |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `PROXY_PORT` | `8081` | listen port |
| `PROXY_HOST` | `0.0.0.0` | listen address |
| `UPSTREAM_PROXY` | (from `https_proxy`) | upstream egress proxy; empty = direct |
| `SELF_SCHEME` | `http` | scheme used when rewriting URLs (set `https` behind an TLS-terminating proxy) |
| `SELF_HOST` | `localhost:<port>` | host used when rewriting URLs |
| `PYTHON` | `python3` | python interpreter |
| `TIMEOUT` | `60` | upstream request timeout (seconds) |
| `RETRY_TIMES` | `3` | auto-retry count on transient drops / 5xx |
| `CACHE_DISABLE` | (unset = on) | set `1` to disable the asset cache |
| `CACHE_MAX` | `2000` | max cached entries (oldest evicted first) |
| `CACHE_TTL` | `43200` | asset cache TTL in seconds (default 12h) |

## nginx option (on a server with direct GitHub access)

```bash
# install nginx (root required)
sudo apt-get install -y nginx         # Debian/Ubuntu
# or: sudo yum install -y nginx       # CentOS/RHEL

sudo cp nginx.conf /etc/nginx/conf.d/github-proxy.conf
sudo nginx -t && sudo nginx -s reload

curl -I http://localhost:8081/
```

> The nginx option needs `ngx_http_sub_module` (usually built in) and direct GitHub access. If your server needs an egress proxy to reach GitHub, use the Python path (`./deploy.sh`, which honors `https_proxy`).

## Notes / limits

- **No login state**: public pages are fine; login / private content can't keep a session because cookies land on a different host (by design)
- Cached assets are content-hashed and immutable, so a 12h TTL is safe; HTML/API responses are never cached
- Subdomains use a `__prefix__` (double-underscore) that GitHub never uses as a real path, so there's no collision

## License

MIT
