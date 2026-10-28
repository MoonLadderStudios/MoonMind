"""Disposable web application and egress gateway for ZAP assessment journeys.

Standard library only, so it runs both beside the test process and inside the
upstream ZAP image. ``/search`` reflects input unescaped while ``/fixed/search``
escapes it and sends hardening headers; ``/account`` needs a session for any
role and ``/admin`` the admin role, either by login session or by the
service's bearer token; ``/away`` redirects off the admitted
origin; the home page's ``/feedback`` form records every post it receives;
``/openapi.json`` describes the API. The gateway forwards only to its
allowlisted hosts, like the scoped egress gateway, and refuses the rest.
"""

from __future__ import annotations

import html
import json
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PASSWORDS = {"alice": "alice-fixture-pass-5044", "root": "root-fixture-pass-5044"}
ROLES = {"alice": "user", "root": "admin", "svc": "user"}
TOKENS = {"svc-fixture-token-5044": "svc"}
HARDENED = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=()",
}
SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "fixture", "version": "1"},
    "servers": [{"url": "/"}],
    "paths": {
        "/api/items": {
            "get": {
                "parameters": [
                    {"name": "name", "in": "query", "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "items"}},
            }
        },
        "/api/fixed/items": {
            "get": {
                "parameters": [
                    {"name": "name", "in": "query", "schema": {"type": "string"}}
                ],
                "responses": {"200": {"description": "items"}},
            }
        },
    },
}


class App(BaseHTTPRequestHandler):
    sessions: dict[str, str] = {}
    posted: list[str] = []
    server_version = "fixture"
    sys_version = ""

    def log_message(self, *_):
        pass

    def _user(self):
        scheme, _, token = (self.headers.get("Authorization") or "").partition(" ")
        if scheme == "Bearer":
            return TOKENS.get(token)
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == "sid" and value in self.sessions:
                return self.sessions[value]
        return None

    def _send(self, code, body, *, kind="text/html", headers=None, hardened=False):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in {**(HARDENED if hardened else {}), **(headers or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _page(self, title, body, **kwargs):
        user = self._user()
        status = f"Signed in as {html.escape(user)}" if user else "Signed out"
        self._send(
            200,
            f"<html><head><title>{title}</title></head><body><p>{status}</p>"
            f"{body}</body></html>",
            **kwargs,
        )

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if path == "/":
            self._page(
                "home",
                '<a href="/search?q=hello">search</a> '
                '<a href="/fixed/search?q=hello">fixed search</a> '
                '<a href="/login">login</a> <a href="/account">account</a> '
                '<a href="/admin">admin</a> <a href="/away">away</a> '
                '<a href="/private/report">private</a>'
                '<form method="POST" action="/feedback"><input name="q"></form>',
            )
        elif path == "/search":
            self._page("search", "<p>Results for " + query.get("q", "") + "</p>")
        elif path == "/fixed/search":
            self._page(
                "search",
                "<p>Results for " + html.escape(query.get("q", "")) + "</p>",
                hardened=True,
            )
        elif path == "/login":
            self._page(
                "login",
                '<form method="POST" action="/login">'
                '<input name="username"><input name="password" type="password">'
                '<input type="submit"></form>',
            )
        elif path == "/account":
            if self._user() is None:
                self._send(302, "", headers={"Location": "/login"})
            else:
                self._page("account", '<a href="/account/settings?tab=main">settings</a>')
        elif path == "/account/settings":
            if self._user() is None:
                self._send(302, "", headers={"Location": "/login"})
            else:
                self._page("settings", "<p>Tab " + query.get("tab", "") + "</p>")
        elif path == "/admin":
            user = self._user()
            if user is None or ROLES.get(user) != "admin":
                self._send(403, "<p>forbidden</p>")
            else:
                self._page("admin", "<p>admin console</p>")
        elif path == "/away":
            self._send(302, "", headers={"Location": "http://outside.example.test/"})
        elif path == "/private/report":
            self._page("private", "<p>excluded route</p>")
        elif path == "/openapi.json":
            self._send(200, json.dumps(SPEC), kind="application/json")
        elif path == "/api/items":
            self._send(
                200,
                "<p>" + query.get("name", "") + "</p>",
            )
        elif path == "/api/fixed/items":
            self._send(
                200,
                json.dumps({"items": [query.get("name", "")]}),
                kind="application/json",
                hardened=True,
            )
        else:
            self._send(404, "<p>not found</p>")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        self.posted.append(urlsplit(self.path).path)
        if urlsplit(self.path).path != "/login":
            self._send(405, "")
            return
        user = form.get("username", "")
        if PASSWORDS.get(user) != form.get("password"):
            self._page("login", "<p>Login failed</p>")
            return
        token = secrets.token_hex(16)
        self.sessions[token] = user
        self._send(
            302, "", headers={"Location": "/account", "Set-Cookie": f"sid={token}; Path=/"}
        )


class Gateway(BaseHTTPRequestHandler):
    """Forwards plain-HTTP proxy requests to allowlisted hosts only."""

    routes: dict[str, tuple[str, int]] = {}
    denied: list[str] = []

    def log_message(self, *_):
        pass

    def _forward(self):
        url = urlsplit(self.path)
        upstream = self.routes.get(f"{url.hostname}:{url.port or 80}")
        if upstream is None:
            self.denied.append(f"{self.command} {url.hostname}")
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        target = url.path + (f"?{url.query}" if url.query else "")
        with socket.create_connection(upstream, timeout=30) as conn:
            head = [f"{self.command} {target} HTTP/1.0"]
            for key, value in self.headers.items():
                if key.lower() not in {"proxy-connection", "connection"}:
                    head.append(f"{key}: {value}")
            conn.sendall(("\r\n".join(head) + "\r\n\r\n").encode() + body)
            response = b""
            while chunk := conn.recv(65536):
                response += chunk
        self.wfile.write(response)

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = do_PATCH = _forward

    def do_CONNECT(self):
        self.denied.append(f"CONNECT {self.path}")
        self.send_response(403)
        self.end_headers()


def serve(handler, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start(app_host: str = "app.example.test", app_port: int = 0, gateway_port: int = 0):
    """Start the app and a gateway admitting only ``app_host``; return ports."""
    app = serve(App, app_port)
    Gateway.routes = {f"{app_host}:{app.server_port}": ("127.0.0.1", app.server_port)}
    gateway = serve(Gateway, gateway_port)
    return app, gateway


if __name__ == "__main__":
    app_server, gateway_server = start(
        app_port=int(sys.argv[1]), gateway_port=int(sys.argv[2])
    )
    threading.Event().wait()
