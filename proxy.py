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

同时重写响应体里的绝对 URL（https://github.com/... -> 走代理），
让点击链接不跳出代理。POST / API 请求透传不阻断。

支持通过 HTTP 代理访问上游（沙箱等需要走 egress 代理的环境）：
  设置环境变量 UPSTREAM_PROXY=http://127.0.0.1:18080 即可。
"""
import gzip
import http.server
import os
import re
import socketserver
import ssl
import sys
import urllib.error
import urllib.request
import zlib

# ---------------- 配置（可由环境变量覆盖） ----------------
LISTEN_HOST = os.environ.get('PROXY_HOST', '0.0.0.0')
LISTEN_PORT = int(os.environ.get('PROXY_PORT', '8081'))
# 上游 HTTP 代理（沙箱里访问 github 需要走 egress 代理时设置）
UPSTREAM_PROXY = os.environ.get('UPSTREAM_PROXY', '').strip()
# 重写后 URL 的协议（默认 http，若挂在前端 https 反代后可设 https）
SELF_SCHEME = os.environ.get('SELF_SCHEME', 'http').strip()
DEFAULT_HOST = os.environ.get('SELF_HOST', 'localhost:{}'.format(LISTEN_PORT))
TIMEOUT = float(os.environ.get('TIMEOUT', '60'))

# 路径前缀 -> 上游域名（路由表）
DOMAIN_ROUTES = {
    '__assets__':           'github.githubassets.com',
    '__raw__':              'raw.githubusercontent.com',
    '__api__':              'api.github.com',
    '__codeload__':         'codeload.github.com',
    '__avatars__':          'avatars.githubusercontent.com',
    '__objects__':          'objects.githubusercontent.com',
    '__gistassets__':      'gist-assets.githubusercontent.com',
    '__userimages__':       'user-images.githubusercontent.com',
    '__privateuserimages__':'private-user-images.githubusercontent.com',
    '__releases__':         'github-releases.githubusercontent.com',
    '__camo__':             'camo.githubusercontent.com',
    '__gist__':             'gist.github.com',
    '__docs__':             'docs.github.com',
}

# 域名 -> 路径前缀（响应体重写用）。主站 github.com 前缀为空。
# 长域名放前面，避免短串先匹配（各域名实际上互不包含，顺序保险）。
DOMAIN_TO_PREFIX = [
    ('gist-assets.githubusercontent.com',         '__gistassets__'),
    ('private-user-images.githubusercontent.com','__privateuserimages__'),
    ('github-releases.githubusercontent.com',    '__releases__'),
    ('user-images.githubusercontent.com',        '__userimages__'),
    ('objects.githubusercontent.com',            '__objects__'),
    ('avatars.githubusercontent.com',            '__avatars__'),
    ('raw.githubusercontent.com',                '__raw__'),
    ('github.githubassets.com',                  '__assets__'),
    ('codeload.github.com',                      '__codeload__'),
    ('api.github.com',                           '__api__'),
    ('docs.github.com',                          '__docs__'),
    ('gist.github.com',                          '__gist__'),
    ('github.com',                               ''),
]

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
# 请求头里要丢弃的（host/len 由我们重设；referer/origin 去掉避免被当外站）
DROP_REQ_HEADERS = {
    'host', 'content-length', 'accept-encoding', 'proxy-connection',
    'connection', 'keep-alive', 'via', 'origin', 'referer',
}

TEXT_RE = re.compile(
    r'text/(html|css|javascript|plain|xml|x-component)'
    r'|application/(javascript|json|xml|x-javascript|ecmascript|atom)',
    re.I,
)

UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def rewrite_text(text, self_url, self_host):
    """重写响应体文本里的 GitHub 绝对 URL -> 走代理。"""
    for domain, prefix in DOMAIN_TO_PREFIX:
        https_src = 'https://' + domain
        proto_src = '//' + domain
        if prefix:
            https_dst = self_url + '/' + prefix
            proto_dst = '//' + self_host + '/' + prefix
        else:
            https_dst = self_url
            proto_dst = '//' + self_host
        # 先替换带 https 的，再替换协议相对 //
        text = text.replace(https_src, https_dst)
        text = text.replace(proto_src, proto_dst)
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


def rewrite_cookie(cookie):
    # 去掉 Domain=.github.com（让 cookie 落到访问的 host）；去 Secure；SameSite=Lax
    cookie = re.sub(r';\s*[Dd]omain=[^;]*', '', cookie)
    cookie = re.sub(r';\s*[Ss]ecure\b', '', cookie)
    cookie = re.sub(r';\s*[Ss]ameSite=[^;]*', '; SameSite=Lax', cookie)
    return cookie


def build_opener():
    handlers = [urllib.request.HTTPSHandler(context=ssl._create_unverified_context())]
    if UPSTREAM_PROXY:
        handlers.insert(0, urllib.request.ProxyHandler({
            'http': UPSTREAM_PROXY,
            'https': UPSTREAM_PROXY,
        }))
    return urllib.request.build_opener(*handlers)


OPENER = build_opener()


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'gh-mirror/1.0'

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
        host = self.headers.get('Host') or DEFAULT_HOST
        return ('{}://{}'.format(SELF_SCHEME, host), host)

    def _send_simple(self, code, msg):
        body = msg.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _handle(self, method):
        self_url, self_host = self._self_urls()

        # 解析路径前缀，确定上游域名；保留 query
        upstream_host = 'github.com'
        upstream_path = self.path
        path_only = self.path.split('?', 1)[0]
        for prefix, domain in DOMAIN_ROUTES.items():
            full_pfx = '/' + prefix
            if path_only == full_pfx:
                upstream_host = domain
                rest = self.path[len(full_pfx):]
                upstream_path = '/' + rest if rest else '/'
                break
            elif path_only.startswith(full_pfx + '/'):
                upstream_host = domain
                upstream_path = self.path[len(full_pfx):]  # 形如 /xxx?y=1
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

        url = 'https://{}{}'.format(upstream_host, upstream_path)
        req = urllib.request.Request(url, data=body, method=method)
        for key, val in self.headers.items():
            k = key.lower()
            if k in DROP_REQ_HEADERS or k in HOP_BY_HOP:
                continue
            try:
                req.add_header(key, val)
            except Exception:
                pass
        req.add_header('Host', upstream_host)
        req.add_header('Accept-Encoding', 'gzip, deflate')
        req.add_header('User-Agent', self.headers.get('User-Agent') or UA)

        try:
            resp = OPENER.open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            resp = e  # HTTPError 也可 read()，含 status/headers
        except Exception as e:
            self._send_simple(502, 'upstream error: {}'.format(e))
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

        self.send_response(status)
        sent = set()
        for key, val in resp.headers.items():
            k = key.lower()
            if k in HOP_BY_HOP or k in DROP_RESP_HEADERS or k in sent:
                continue
            if k == 'location':
                val = rewrite_location(val, self_url, self_host)
            elif k == 'set-cookie':
                val = rewrite_cookie(val)
            self.send_header(key, val)
            sent.add(k)
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        if method != 'HEAD':
            try:
                self.wfile.write(out)
            except Exception:
                pass


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print('GitHub reverse proxy listening on http://{}:{} -> github.com'.format(
        LISTEN_HOST, LISTEN_PORT))
    print('  upstream proxy : {}'.format(UPSTREAM_PROXY or 'direct (no proxy)'))
    print('  self scheme    : {}'.format(SELF_SCHEME))
    print('  routes         : github.com + {} sub-domains'.format(len(DOMAIN_ROUTES)))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nshutting down...')
        server.shutdown()


if __name__ == '__main__':
    main()
