#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 全站反向代理（单文件 Python 实现，无需 nginx / 无第三方依赖）

监听端口（默认 8081），把 github.com 及其相关子域反代到本地：
  - 主站 github.com
  - 静态资源 github.githubassets.com   -> 路径前缀 /__assets__
  - raw.githubusercontent.com          -> /__raw__
  - api.github.com                     -> /__api__
  - codeload.github.com               -> /__codeload__
  - avatars / objects / user-images ... 等其他子域

支持 GitHub 登录：
  - Cookie 自动重写 Domain / Secure / SameSite
  - 登录流程中所有子域跳转自动重写
  - CSRF Token / 会话 Cookie 全程透传

支持通过 HTTP 代理访问上游（沙箱等需要走 egress 代理的环境）：
  设置环境变量 UPSTREAM_PROXY=http://127.0.0.1:18080 即可。

可靠性改进（v2.0）：
  - 上游瞬时断连/5xx 自动重试（指数退避）
  - 静态资源内存缓存，二次访问零上游请求
  - 客户端断连（BrokenPipe）不再让工作线程崩溃
  - 重写 JSON 里转义形式的 https:\/\/... 链接
  - 健康检查端点 GET /__health__
  - 支持 GitHub 登录会话保持

v2.1 登录链路修复：
  - 保留全部多段 Set-Cookie（此前响应头去重会丢会话 cookie，登录必失败）
  - Origin/Referer 反写为上游地址，通过 GitHub CSRF 同源校验（此前登录 POST 被 422 拒绝）
  - 新增 login.github.com / alive.github.com 子域路由（2FA / 设备验证 / SSO 跳转）
"""
import gzip
import http.server
import os
import http.client
import re
import socketserver
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from urllib.parse import urlsplit

# ---------------- 配置（可由环境变量覆盖） ----------------
LISTEN_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')
LISTEN_PORT = int(os.environ.get('PROXY_PORT', '8081'))
# 上游 HTTP 代理（沙箱里访问 github 需要走 egress 代理时设置）
UPSTREAM_PROXY = os.environ.get('UPSTREAM_PROXY', '').strip()
# 重写后 URL 的协议（默认 http，若挂在前端 https 反代后可设 https）
SELF_SCHEME = os.environ.get('SELF_SCHEME', 'http').strip()
_SELF_HOST_ENV = os.environ.get('SELF_HOST', '').strip()
DEFAULT_HOST = _SELF_HOST_ENV or 'localhost:{}'.format(LISTEN_PORT)
# 显式设置 SELF_HOST 时强制用它重写，忽略请求里的 Host 头
# （部署在反向代理/网关后面时，Host 会被网关改成内部地址，导致页面资源指向不可达域名）
FORCE_SELF_HOST = bool(_SELF_HOST_ENV)
TIMEOUT = float(os.environ.get('TIMEOUT', '18'))
# 上游瞬时错误重试次数（含复用死连接的立即重连）
RETRY_TIMES = int(os.environ.get('RETRY_TIMES', '3'))
# 静态资源内存缓存（CACHE_DISABLE=1 可关）
CACHE_ENABLED = os.environ.get('CACHE_DISABLE', '').lower() not in ('1', 'true', 'yes')
CACHE_MAX = int(os.environ.get('CACHE_MAX', '3000'))
CACHE_TTL = float(os.environ.get('CACHE_TTL', str(12 * 3600)))
# 匿名页面（无 Cookie 的 GET）短缓存秒数，0 表示关闭
HTML_CACHE_TTL = float(os.environ.get('HTML_CACHE_TTL', '120'))
# 每个上游域名保留的空闲连接数（keep-alive 复用，省掉跨洋 TLS 握手）
POOL_MAX_PER_HOST = int(os.environ.get('POOL_MAX_PER_HOST', '8'))
POOL_IDLE_MAX = 90.0
# 回传给浏览器的文本是否 gzip 压缩
GZIP_OUT = os.environ.get('GZIP_OUT', '1').lower() not in ('0', 'false', 'no')
# 启动时预热首页并预取静态资源
WARMUP = os.environ.get('WARMUP', '1').lower() not in ('0', 'false', 'no')

# 路径前缀 -> 上游域名（路由表）
DOMAIN_ROUTES = {
    '__assets__':           'github.githubassets.com',
    '__raw__':              'raw.githubusercontent.com',
    '__api__':              'api.github.com',
    '__codeload__':         'codeload.github.com',
    '__avatars__':          'avatars.githubusercontent.com',
    '__objects__':          'objects.githubusercontent.com',
    '__gistassets__':       'gist-assets.githubusercontent.com',
    '__userimages__':       'user-images.githubusercontent.com',
    '__privateuserimages__':'private-user-images.githubusercontent.com',
    '__releases__':         'github-releases.githubusercontent.com',
    '__camo__':             'camo.githubusercontent.com',
    '__gist__':             'gist.github.com',
    '__docs__':             'docs.github.com',
    '__skills__':           'skills.github.com',
    '__education__':        'education.github.com',
    '__shop__':             'shop.github.com',
    '__login__':            'login.github.com',
    '__alive__':            'alive.github.com',
}

# 域名 -> 路径前缀（响应体重写用）。主站 github.com 前缀为空。
# 长域名放前面，避免短串先匹配。
DOMAIN_TO_PREFIX = [
    ('gist-assets.githubusercontent.com',         '__gistassets__'),
    ('private-user-images.githubusercontent.com','__privateuserimages__'),
    ('github-releases.githubusercontent.com',      '__releases__'),
    ('user-images.githubusercontent.com',          '__userimages__'),
    ('objects.githubusercontent.com',              '__objects__'),
    ('avatars.githubusercontent.com',              '__avatars__'),
    ('raw.githubusercontent.com',                  '__raw__'),
    ('github.githubassets.com',                   '__assets__'),
    ('codeload.github.com',                       '__codeload__'),
    ('api.github.com',                            '__api__'),
    ('docs.github.com',                           '__docs__'),
    ('gist.github.com',                           '__gist__'),
    ('skills.github.com',                         '__skills__'),
    ('education.github.com',                      '__education__'),
    ('shop.github.com',                           '__shop__'),
    ('login.github.com',                          '__login__'),
    ('alive.github.com',                          '__alive__'),
    ('github.com',                                ''),
]

# 哪些上游域名的 GET 响应可以安全缓存（内容按路径指纹固定，不含个性化会话）
CACHEABLE_HOSTS = {
    'github.githubassets.com',
    'avatars.githubusercontent.com',
    'camo.githubusercontent.com',
    'user-images.githubusercontent.com',
    'private-user-images.githubusercontent.com',
    'objects.githubusercontent.com',
}

HOP_BY_HOP = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'proxy-connection',
}
# 响应头里要删除的（避免 CSP/HSTS 拦截资源加载；Content-Length/Encoding 会重算）
DROP_RESP_HEADERS = {
    'content-security-policy', 'content-security-policy-report-only',
    'x-frame-options', 'strict-transport-security', 'x-content-security-policy',
    'x-webkit-csp', 'content-length', 'content-encoding', 'x-xss-protection',
    'report-to', 'nel',
}
# 请求头里要丢弃的（host/len 由我们重设）
DROP_REQ_HEADERS = {
    'host', 'content-length', 'accept-encoding', 'proxy-connection',
    'connection', 'keep-alive', 'via',
}

TEXT_RE = re.compile(
    r'text/(html|css|javascript|plain|xml|x-component)'
    r'|application/(javascript|json|xml|x-javascript|ecmascript|atom)',
    re.I,
)
# 可缓存资源的 Content-Type（CSS/JS/字体/图片/二进制对象）
CACHEABLE_CT_RE = re.compile(
    r'text/(css|javascript)|application/(javascript|octet-stream)'
    r'|font/|image/',
    re.I,
)
# 回传给浏览器时值得 gzip 的类型（上游压缩已被丢弃，不重压就是明文传输）
COMPRESSIBLE_RE = re.compile(
    r'text/|application/(json|javascript|xml|ecmascript|x-javascript)',
    re.I,
)

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


# ---------------- 内存缓存 ----------------
_asset_cache = {}          # key -> (expire_ts, status, [(k,v)], body)
_html_cache = {}           # key -> (expire_ts, status, [(k,v)], body)  匿名页面短缓存
_cache_lock = threading.Lock()
_start_ts = time.time()
# 最近请求诊断：排查缓存未命中的真实原因（path / cookie / 命中结果）
import collections
_recent = collections.deque(maxlen=8)


def _cache_key(upstream_host, upstream_path):
    return upstream_host + upstream_path


def cache_get(key):
    with _cache_lock:
        item = _asset_cache.get(key)
        if not item:
            return None
        expire_ts, status, headers, body = item
        if expire_ts < time.time():
            _asset_cache.pop(key, None)
            return None
        return status, headers, body


def cache_put(key, status, headers, body):
    if not CACHE_ENABLED or status != 200:
        return
    with _cache_lock:
        if len(_asset_cache) >= CACHE_MAX:
            try:
                oldest = next(iter(_asset_cache))
                _asset_cache.pop(oldest, None)
            except StopIteration:
                pass
        _asset_cache[key] = (time.time() + CACHE_TTL, status, headers, body)


# 真正的登录态标志。注意 _gh_sess 匿名访客也有、logged_in=no 也是匿名，
# 都不能算登录，否则页面缓存永远写不进去。
SESSION_COOKIE_RE = re.compile(r'user_session=|logged_in=yes', re.I)


def _is_anon(cookie_header):
    return not SESSION_COOKIE_RE.search(cookie_header or '')


def _strip_gateway_params(path):
    """去掉托管网关注入的鉴权参数（sharecode）。
    它只对访问控制有意义，传给上游没用，还会让缓存 key 每次都不一样。"""
    if 'sharecode=' not in path:
        return path
    base, sep, query = path.partition('?')
    if not sep:
        return path
    kept = [p for p in query.split('&') if p and not p.startswith('sharecode=')]
    return base + ('?' + '&'.join(kept) if kept else '')


def html_cache_get(key):
    if HTML_CACHE_TTL <= 0:
        return None
    with _cache_lock:
        item = _html_cache.get(key)
        if not item:
            return None
        expire_ts, status, headers, body = item
        if expire_ts < time.time():
            _html_cache.pop(key, None)
            return None
        return status, headers, body


def html_cache_put(key, status, headers, body):
    if HTML_CACHE_TTL <= 0 or not CACHE_ENABLED or status != 200:
        return
    with _cache_lock:
        if len(_html_cache) >= 500:
            try:
                _html_cache.pop(next(iter(_html_cache)))
            except StopIteration:
                pass
        _html_cache[key] = (time.time() + HTML_CACHE_TTL, status, headers, body)


# ---------------- URL 重写 ----------------
def _esc(text):
    """把 http://host 转成 JSON 里合法的转义形式 http:\/\/host。"""
    return text.replace('/', '\\/')


def rewrite_text(text, self_url, self_host):
    """重写响应体文本里的 GitHub 绝对 URL -> 走代理。"""
    # 1) 普通形式 https://domain 与协议相对 //domain
    for domain, prefix in DOMAIN_TO_PREFIX:
        https_src = 'https://' + domain
        proto_src = '//' + domain
        if prefix:
            https_dst = self_url + '/' + prefix
            proto_dst = '//' + self_host + '/' + prefix
        else:
            https_dst = self_url
            proto_dst = '//' + self_host
        text = text.replace(https_src, https_dst)
        text = text.replace(proto_src, proto_dst)
        # 2) JSON 转义形式 https:\/\/domain
        for domain, prefix in DOMAIN_TO_PREFIX:
            esc_src = 'https:\\/\\/' + domain
            if prefix:
                esc_dst = _esc(self_url) + '\\/' + prefix
            else:
                esc_dst = _esc(self_url)
            text = text.replace(esc_src, esc_dst)
    # 3) 剥掉 SRI integrity 属性：URL 被我们改写过，原始 hash 必然对不上，
    #    浏览器会因此拒绝执行全部 JS/CSS（GitHub 于是提示"Javascript 被阻挡"）
    text = re.sub(r'\s+integrity\s*=\s*"[^"]*"', '', text, flags=re.I)
    text = re.sub(r"\s+integrity\s*=\s*'[^']*'", '', text, flags=re.I)
    text = re.sub(r'\s+integrity\s*=\s*[A-Za-z0-9_\-]+', '', text, flags=re.I)
    return text


def rewrite_location(loc, self_url, self_host):
    for domain, prefix in DOMAIN_TO_PREFIX:
        https_src = 'https://' + domain
        if prefix:
            https_dst = self_url + '/' + prefix
            proto_dst = '//' + self_host + '/' + prefix
        else:
            https_dst = self_url
            proto_dst = '//' + self_host
        loc = loc.replace(https_src, https_dst)
        loc = loc.replace('//' + domain, proto_dst)
    return loc


def rewrite_to_upstream(url, self_url, self_host):
    """把浏览器发给代理的 URL（Referer 等）反向还原成上游 URL，
    供请求头重写使用。仅处理指向代理自身的 URL，其余原样返回。"""
    rest = None
    if url.startswith(self_url):
        rest = url[len(self_url):]
    elif url.startswith('//' + self_host):
        rest = url[2 + len(self_host):]
    elif url.startswith('http://' + self_host):
        rest = url[7 + len(self_host):]
    elif url.startswith('https://' + self_host):
        rest = url[8 + len(self_host):]
    else:
        return url
    for pfx, domain in DOMAIN_ROUTES.items():
        full = '/' + pfx + '/'
        if rest.startswith(full):
            return 'https://{}{}'.format(domain, rest[len(full) - 1:])
        if rest == '/' + pfx:
            return 'https://{}/'.format(domain)
    return 'https://github.com' + (rest if rest.startswith('/') else '/' + rest)


def rewrite_cookie(cookie):
    """重写 Cookie：去掉 Domain 属性让 cookie 落到当前 host；
    去掉 Secure（http 代理下浏览器不会发送 Secure cookie）；
    SameSite 设为 Lax 允许跨站跳转携带 cookie。"""
    # 去掉 Domain=.github.com 等
    cookie = re.sub(r';\s*[Dd]omain=[^;]*', '', cookie)
    # 去掉 Secure 属性
    cookie = re.sub(r';\s*[Ss]ecure\b', '', cookie)
    # SameSite 统一设为 Lax（允许顶层导航携带 cookie）
    cookie = re.sub(r';\s*[Ss]ameSite=[^;]*', '; SameSite=Lax', cookie)
    return cookie.strip()


_SSL_CTX = ssl._create_unverified_context()

_PROXY = None
if UPSTREAM_PROXY:
    _PROXY = urlsplit(UPSTREAM_PROXY if '://' in UPSTREAM_PROXY else 'http://' + UPSTREAM_PROXY)

# 上游连接池：host -> [(conn, idle_since_ts)]
# 复用 keep-alive 连接，省掉每个资源一次跨洋 TLS 握手
_conn_pool = {}
_pool_lock = threading.Lock()


class _Resp(object):
    """轻量响应对象，接口与 urllib 的响应保持兼容。"""
    __slots__ = ('status', 'code', 'headers', '_body')

    def __init__(self, status, headers, body):
        self.status = status
        self.code = status
        self.headers = headers
        self._body = body

    def read(self, *args):
        return self._body


def _new_conn(host):
    conn = http.client.HTTPSConnection(host, 443, timeout=TIMEOUT, context=_SSL_CTX)
    if _PROXY:
        tunnel_headers = {}
        if _PROXY.username:
            import base64
            raw = '{}:{}'.format(_PROXY.username, _PROXY.password or '').encode('utf-8')
            tunnel_headers['Proxy-Authorization'] = 'Basic ' + base64.b64encode(raw).decode('ascii')
        conn.set_tunnel(_PROXY.hostname, _PROXY.port or 80, headers=tunnel_headers)
    return conn


def _take_conn(host):
    now = time.time()
    with _pool_lock:
        lst = _conn_pool.get(host)
        if lst:
            while lst:
                conn, ts = lst.pop()
                if now - ts <= POOL_IDLE_MAX:
                    return conn
                try:
                    conn.close()
                except Exception:
                    pass
    return _new_conn(host)


def _give_conn(host, conn, reusable):
    if not reusable:
        try:
            conn.close()
        except Exception:
            pass
        return
    with _pool_lock:
        lst = _conn_pool.setdefault(host, [])
        if len(lst) < POOL_MAX_PER_HOST:
            lst.append((conn, time.time()))
            return
    try:
        conn.close()
    except Exception:
        pass


def _drop_pool(host):
    """上游把空闲连接关掉后，池里的连接会变成死连接；一旦写失败就整池丢弃重建。"""
    with _pool_lock:
        lst = _conn_pool.pop(host, [])
    for conn, _ in lst:
        try:
            conn.close()
        except Exception:
            pass


# 回源限流：把对 GitHub 的请求节奏压到接近真人浏览，降低"速度异常"判定概率
UPSTREAM_CONCURRENCY = int(os.environ.get('UPSTREAM_CONCURRENCY', '4'))
UPSTREAM_MIN_INTERVAL = float(os.environ.get('UPSTREAM_MIN_INTERVAL', '0.12'))
_upstream_sem = threading.BoundedSemaphore(UPSTREAM_CONCURRENCY)
_rate_lock = threading.Lock()
_last_upstream_ts = 0.0


def throttled_request(req):
    """带并发上限与最小间隔的上游请求。缓存命中的请求不经过这里，不受影响。"""
    global _last_upstream_ts
    with _upstream_sem:
        with _rate_lock:
            wait = UPSTREAM_MIN_INTERVAL - (time.time() - _last_upstream_ts)
            if wait > 0:
                time.sleep(wait)
            _last_upstream_ts = time.time()
        return upstream_request(req)


def upstream_request(req):
    """请求上游，复用 keep-alive 连接，瞬时错误自动重试。返回 (resp, error)。"""
    url = urlsplit(req.full_url)
    host = url.hostname
    path = url.path or '/'
    if url.query:
        path += '?' + url.query
    method = (req.method or 'GET').upper()
    hdrs = list(req.header_items())
    body = req.data
    last_err = None

    for attempt in range(RETRY_TIMES):
        conn = _take_conn(host)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in hdrs:
                conn.putheader(k, v)
            conn.endheaders(message_body=body)
            r = conn.getresponse()
            data = r.read()
            reusable = not r.will_close
            _give_conn(host, conn, reusable)
            if r.status >= 500 and attempt < RETRY_TIMES - 1:
                time.sleep(0.15 * (attempt + 1))
                continue
            return _Resp(r.status, r.headers, data), None
        except Exception as e:
            last_err = e
            _give_conn(host, conn, False)
            _drop_pool(host)
            if attempt < RETRY_TIMES - 1:
                time.sleep(0.15 * (attempt + 1))
            else:
                return None, e
    return None, last_err
    return None, None


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'gh-mirror/2.1'

    def log_message(self, fmt, *args):
        sys.stderr.write("[proxy] %s %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):     self._handle('GET')
    def do_HEAD(self):    self._handle('HEAD')
    def do_POST(self):    self._handle('POST')
    def do_PUT(self):     self._handle('PUT')
    def do_DELETE(self):  self._handle('DELETE')
    def do_PATCH(self):   self._handle('PATCH')
    def do_OPTIONS(self): self._handle('OPTIONS')

    def _self_urls(self):
        if FORCE_SELF_HOST:
            return ('{}://{}'.format(SELF_SCHEME, DEFAULT_HOST), DEFAULT_HOST)
        host = self.headers.get('Host') or DEFAULT_HOST
        return ('{}://{}'.format(SELF_SCHEME, host), host)

    def _safe_send(self, data):
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            pass

    def _send_simple(self, code, msg):
        body = msg.encode('utf-8')
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return
        if self.command != 'HEAD':
            self._safe_send(body)

    def _handle(self, method):
        self_url, self_host = self._self_urls()

        # 健康检查端点
        clean_path = _strip_gateway_params(self.path)
        path_only = clean_path.split('?', 1)[0]
        if path_only == '/__health__':
            import json
            payload = json.dumps({
                'status': 'ok',
                'uptime_s': round(time.time() - _start_ts, 1),
                'cached_entries': len(_asset_cache) + len(_html_cache),
                'recent': list(_recent),
                'upstream_proxy': bool(UPSTREAM_PROXY),
                'version': '2.1',
            }).encode('utf-8')
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self._safe_send(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # 解析路径前缀，确定上游域名；保留 query
        upstream_host = 'github.com'
        upstream_path = clean_path
        for prefix, domain in DOMAIN_ROUTES.items():
            full_pfx = '/' + prefix
            if path_only == full_pfx:
                upstream_host = domain
                rest = clean_path[len(full_pfx):]
                upstream_path = '/' + rest if rest else '/'
                break
            elif path_only.startswith(full_pfx + '/'):
                upstream_host = domain
                upstream_path = clean_path[len(full_pfx):]
                break
        if not upstream_path.startswith('/'):
            upstream_path = '/' + upstream_path

        # 读请求体（POST/PATCH 等）
        body = None
        cl = self.headers.get('Content-Length')
        if cl:
            try:
                body = self.rfile.read(int(cl))
            except Exception:
                body = None

        # 命中静态资源缓存（GET 且上游域名可缓存）直接回源
        cache_key = None
        cached = None
        if method == 'GET' and upstream_host in CACHEABLE_HOSTS:
            cache_key = _cache_key(upstream_host, upstream_path)
            cached = cache_get(cache_key)
            if cached:
                status, headers, out = cached
                self._send(status, headers, out, method)
                return

        # 匿名页面短缓存：未登录访客共享同一份已重写好的页面，二次打开免回源
        html_key = None
        if (method == 'GET' and HTML_CACHE_TTL > 0
                and _is_anon(self.headers.get('Cookie'))):
            html_key = 'html|' + _cache_key(upstream_host, upstream_path)
            hit = html_cache_get(html_key)
            _recent.append({
                'p': upstream_path[:110], 'hit': 1 if hit else 0,
                'ck': (self.headers.get('Cookie') or '')[:50]})
            if hit:
                status, hit_headers, out = hit
                self._send(status, hit_headers, out, method)
                return
        else:
            _recent.append({
                'p': upstream_path[:110], 'hit': -1,
                'ck': (self.headers.get('Cookie') or '')[:50]})
            html_key = None

        url = 'https://{}{}'.format(upstream_host, upstream_path)
        req = urllib.request.Request(url, data=body, method=method)
        # 浏览器发起的请求里，Origin/Referer 指向代理自身（例如
        # http://localhost:8081）；GitHub 的 CSRF 校验要求与上游同源，
        # 必须把它们反写成上游地址，否则登录 POST 会被 422 拒绝。
        upstream_origin = 'https://{}'.format(upstream_host)
        for key, val in self.headers.items():
            k = key.lower()
            if k in DROP_REQ_HEADERS or k in HOP_BY_HOP:
                continue
            if k == 'origin':
                val = upstream_origin
            elif k == 'referer':
                val = rewrite_to_upstream(val, self_url, self_host)
            try:
                req.add_header(key, val)
            except Exception:
                pass
        req.add_header('Host', upstream_host)
        req.add_header('Accept-Encoding', 'gzip, deflate')
        req.add_header('User-Agent', self.headers.get('User-Agent') or UA)

        resp, err = throttled_request(req)
        if err is not None:
            self._send_simple(502, 'upstream error: {}'.format(err))
            return

        try:
            status = resp.status
        except AttributeError:
            status = getattr(resp, 'code', 200)

        try:
            raw = resp.read()
        except Exception:
            raw = b''

        # 解压
        ce = (resp.headers.get('Content-Encoding') or '').lower()
        if ce == 'gzip' and raw:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        elif ce == 'deflate' and raw:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                try:
                    raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    pass

        ctype = resp.headers.get('Content-Type', '')
        if TEXT_RE.search(ctype):
            try:
                text = raw.decode('utf-8', errors='replace')
            except Exception:
                text = raw.decode('latin-1', errors='replace')
            out = rewrite_text(text, self_url, self_host).encode('utf-8')
        else:
            out = raw

        # 收集要回传的响应头（重写 location / cookie）
        resp_headers = []
        for key, val in resp.headers.items():
            k = key.lower()
            if k in HOP_BY_HOP or k in DROP_RESP_HEADERS:
                continue
            if k == 'location':
                val = rewrite_location(val, self_url, self_host)
            elif k == 'set-cookie':
                val = rewrite_cookie(val)
            resp_headers.append((k, val))

        # 可缓存资源写入缓存
        if (cache_key is not None and method == 'GET' and status == 200
                and CACHEABLE_CT_RE.search(ctype)):
            cache_put(cache_key, status, resp_headers, out)

        # 匿名页面写入短缓存。GitHub 首页会下发 _gh_sess 这类匿名会话 cookie，
        # 不能因为看到 Set-Cookie 就放弃缓存；只有真正的登录态 cookie 才排除。
        if (html_key is not None and status == 200
                and 'text/html' in ctype.lower()):
            setck = ' '.join(v for k, v in resp_headers if k == 'set-cookie')
            if not SESSION_COOKIE_RE.search(setck):
                cache_headers = [(k, v) for k, v in resp_headers if k != 'set-cookie']
                html_cache_put(html_key, status, cache_headers, out)

        self._send(status, resp_headers, out, method)

    def _send(self, status, resp_headers, out, method):
        """回传前按需 gzip 压缩：上游压过的响应在解压重写后已变回明文，
        不重新压缩等于把 550KB 原样推给浏览器。"""
        if GZIP_OUT and method != 'HEAD' and len(out) >= 1024:
            ae = (self.headers.get('Accept-Encoding') or '').lower()
            if 'gzip' in ae:
                ctype = ''
                for k, v in resp_headers:
                    if k.lower() == 'content-type':
                        ctype = v
                        break
                if COMPRESSIBLE_RE.search(ctype or ''):
                    try:
                        packed = gzip.compress(out, 5)
                        if len(packed) < len(out) * 0.95:
                            resp_headers = [(k, v) for k, v in resp_headers
                                            if k.lower() not in ('content-encoding', 'vary')]
                            resp_headers.append(('content-encoding', 'gzip'))
                            resp_headers.append(('vary', 'Accept-Encoding'))
                            out = packed
                    except Exception:
                        pass
        self._respond(status, resp_headers, out, method)

    def _respond(self, status, resp_headers, out, method):
        try:
            self.send_response(status)
            sent = set()
            for k, val in resp_headers:
                # Set-Cookie 必须全部回传：GitHub 登录响应会同时下发
                # _gh_sess / logged_in / dotcom_user / host_user 等多段 cookie，
                # 去重会丢掉会话 cookie 导致登录失效。
                if k == 'set-cookie':
                    self.send_header(k, val)
                    continue
                if k in sent:
                    continue
                self.send_header(k, val)
                sent.add(k)
            self.send_header('Content-Length', str(len(out)))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return
        if method != 'HEAD':
            self._safe_send(out)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------- 启动预热 ----------------
def _prefetch(host, path):
    """启动预热用：拉一个上游资源，返回 (resp, raw_body) 或 None。"""
    try:
        req = urllib.request.Request('https://{}{}'.format(host, path), method='GET')
        req.add_header('Host', host)
        req.add_header('Accept-Encoding', 'gzip, deflate')
        req.add_header('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8')
        req.add_header('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8')
        req.add_header('User-Agent', UA)
        resp, err = throttled_request(req)
        if err is not None or resp is None:
            return None
        raw = resp.read()
        ce = (resp.headers.get('Content-Encoding') or '').lower()
        if ce == 'gzip' and raw:
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        elif ce == 'deflate' and raw:
            try:
                raw = zlib.decompress(raw)
            except Exception:
                pass
        return resp, raw
    except Exception:
        return None


def _clean_headers(resp):
    out = []
    for k, v in resp.headers.items():
        kl = k.lower()
        if kl in HOP_BY_HOP or kl in DROP_RESP_HEADERS or kl == 'set-cookie':
            continue
        out.append((kl, v))
    return out


def _warmup_job(self_url, self_host):
    """预热首页 + 首屏静态资源，让第一个访客也能秒开。"""
    page = _prefetch('github.com', '/')
    if not page:
        print('[warmup] 首页预热失败（上游不可达），跳过')
        sys.stdout.flush()
        return
    resp, raw = page
    try:
        status = resp.status
    except AttributeError:
        status = getattr(resp, 'code', 200)
    text = raw.decode('utf-8', errors='replace')
    out = rewrite_text(text, self_url, self_host).encode('utf-8')
    html_cache_put('html|github.com/', status, _clean_headers(resp), out)

    # 原始 HTML 里资源可能写成绝对 URL，也可能是 /assets/ 相对路径，两种都收
    seen = set()
    for m in re.finditer(
            r'''(?:https://github\.githubassets\.com)?/assets/[A-Za-z0-9_@.\-/]+\.(?:css|js)''',
            text):
        p = m.group(0)
        if p.startswith('https://'):
            p = p[len('https://github.githubassets.com'):]
        if p not in seen:
            seen.add(p)
    paths = list(seen)[:80]
    ok = [0]

    def worker(sub):
        try:
            r = _prefetch('github.githubassets.com', sub)
            if not r:
                return
            rr, body = r
            if getattr(rr, 'status', 200) != 200:
                return
            ct = rr.headers.get('Content-Type', '')
            if not CACHEABLE_CT_RE.search(ct):
                return
            cache_put(_cache_key('github.githubassets.com', sub), 200,
                      _clean_headers(rr), body)
            ok[0] += 1
        except Exception:
            pass

    for i in range(0, len(paths), 6):
        batch = [threading.Thread(target=worker, args=(p,)) for p in paths[i:i + 6]]
        for t in batch:
            t.start()
        for t in batch:
            t.join()
    print('[warmup] 首页已缓存，静态资源预取 {}/{}'.format(ok[0], len(paths)))
    sys.stdout.flush()


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print('GitHub reverse proxy v2.1 (with login support)')
    print('  listening on  : http://{}:{} -> github.com'.format(
        LISTEN_HOST, LISTEN_PORT))
    print('  upstream proxy: {}'.format(UPSTREAM_PROXY or 'direct (no proxy)'))
    print('  self scheme   : {}'.format(SELF_SCHEME))
    print('  asset cache   : {} ({} entries, ttl {:.0f}s)'.format(
        'on' if CACHE_ENABLED else 'off', CACHE_MAX, CACHE_TTL))
    print('  retry times   : {}'.format(RETRY_TIMES))
    print('  routes        : github.com + {} sub-domains'.format(len(DOMAIN_ROUTES)))
    print('  login support : enabled (cookie domain rewritten)')
    print('  page cache    : {:.0f}s  gzip out: {}  warmup: {}'.format(
        HTML_CACHE_TTL, 'on' if GZIP_OUT else 'off', 'on' if WARMUP else 'off'))
    sys.stdout.flush()
    if WARMUP:
        threading.Thread(
            target=_warmup_job,
            args=('{}://{}'.format(SELF_SCHEME, DEFAULT_HOST), DEFAULT_HOST),
            daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nshutting down...')
        server.shutdown()


if __name__ == '__main__':
    main()
