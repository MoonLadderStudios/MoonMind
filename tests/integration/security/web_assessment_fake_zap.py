"""Deterministic stand-in for ``zap.sh -cmd -autorun`` at the binary boundary.

It executes the Automation Framework plan the worker wrote: the request
guard script, context include and exclude regexes, form users whose password
is the ``${...}`` placeholder resolved from the environment, poll
verification, requestor, OpenAPI import, spider (posting forms when the plan
allows), passive header checks, reflected-input active checks at the plan's
strength, traditional-json-plus reports and HAR exports of the context, which
omit refused requests as ZAP's do. Requests go through
the upstream proxy given by ``-config``, exactly as the launch script passes
it, no faster than its network rate-limit rule allows. ``FIXTURE_ZAP_FAULT``
selects a failure: ``malformed`` output, a plan ``warning`` (exit 2), or
``cancel`` after the passive phase.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

PAYLOAD = "<script>alert(5044)</script>"


def java_regex(value: str) -> re.Pattern:
    return re.compile(re.sub(r"\\Q(.*?)\\E", lambda m: re.escape(m.group(1)), value))


def local(path: str) -> str:
    for prefix, env in (
        ("/outputs/", "FIXTURE_OUTPUTS"),
        ("/inputs/", "FIXTURE_INPUTS"),
    ):
        if path.startswith(prefix):
            return os.path.join(os.environ[env], path[len(prefix) :])
    return path


def resolve(value):
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)
    return value


class Zap:
    def __init__(self, plan: dict, proxy: tuple[str, int] | None, rate: int | None):
        self.plan = plan
        self.proxy = proxy
        self.interval = 1 / rate if rate else 0.0
        self.last_sent = 0.0
        (self.context,) = plan["env"]["contexts"]
        self.include = [java_regex(p) for p in self.context.get("includePaths", [])]
        self.exclude = [java_regex(p) for p in self.context.get("excludePaths", [])]
        self.users = {u["name"]: u for u in self.context.get("users", [])}
        self.sessions: dict[str, str] = {}
        self.messages: list[dict] = []
        self.alerts: dict[tuple, dict] = {}
        self.sites: list[str] = []
        self.polled = 0
        self.guard = None
        for job in plan["jobs"]:
            if job["type"] == "script" and job["parameters"]["type"] == "httpsender":
                # The guard's first line declares its admitted scope.
                first = job["parameters"]["inline"].split("\n", 1)[0]
                self.guard = json.loads(first.removeprefix("var SCOPE = ")[:-1])

    def guarded(self, method: str, url: str) -> str:
        """The URL the guard script sends a request to, as ZAP runs it."""
        parts = urlsplit(url)
        scope = self.guard
        if scope is None or (parts.hostname or "") != scope["host"]:
            return url
        path = parts.path or "/"

        def within(prefix):
            return prefix == "/" or path == prefix or path.startswith(prefix + "/")

        if not any(within(e) for e in scope["exclusions"]) and any(
            within(r["path"]) and method in r["methods"] for r in scope["routes"]
        ):
            return url
        return f"http://{scope['denied']}{path}"

    def in_scope(self, url: str) -> bool:
        return any(p.fullmatch(url) for p in self.include) and not any(
            p.fullmatch(url) for p in self.exclude
        )

    def send(self, method: str, url: str, *, user=None, body: str = "") -> dict:
        if user is not None:
            self.ensure_session(user)
        url = self.guarded(method, url)
        headers = {"host": urlsplit(url).netloc}
        if user is not None and user in self.sessions:
            headers["Cookie"] = self.sessions[user]
        bearer = os.environ.get("ZAP_AUTH_HEADER_VALUE")
        site = os.environ.get("ZAP_AUTH_HEADER_SITE", "")
        # ZAP matches the site against the request's host name, not its port.
        if bearer and site in (urlsplit(url).hostname or ""):
            headers[os.environ.get("ZAP_AUTH_HEADER", "Authorization")] = bearer
        if body:
            headers["content-type"] = "application/x-www-form-urlencoded"
        status, response_headers, text = 0, [], ""
        time.sleep(max(0.0, self.last_sent + self.interval - time.monotonic()))
        self.last_sent = time.monotonic()
        started = datetime.now(UTC).isoformat(timespec="milliseconds")
        try:
            host, port = self.proxy or (
                urlsplit(url).hostname,
                urlsplit(url).port or 80,
            )
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request(
                method, url if self.proxy else urlsplit(url).path, body or None, headers
            )
            response = conn.getresponse()
            status = response.status
            response_headers = response.getheaders()
            text = response.read().decode(errors="replace")
            conn.close()
        except OSError:
            pass  # recorded as a network failure (status 0)
        message = {
            "startedDateTime": started,
            "request": {
                "method": method,
                "url": url,
                "headers": [{"name": k, "value": v} for k, v in headers.items()],
                "cookies": [],
                "postData": {"mimeType": headers.get("content-type", ""), "text": body},
            },
            "response": {
                "status": status,
                "headers": [{"name": k, "value": v} for k, v in response_headers],
                "content": {"text": text},
            },
        }
        self.messages.append(message)
        if status and self.in_scope(url):
            self.passive(message)
        return message

    def ensure_session(self, name: str) -> None:
        auth = self.context.get("authentication")
        if auth is None or name in self.sessions:
            return
        user = self.users[name]
        verification = auth["verification"]
        poll = verification["pollUrl"]
        marker = java_regex(verification["loggedInRegex"])
        self.sessions[name] = ""
        before = self.send("GET", poll, user=name)
        if marker.search(before["response"]["content"]["text"]):
            return
        params = auth["parameters"]
        body = (
            params["loginRequestBody"]
            .replace("{%username%}", user["credentials"]["username"])
            .replace("{%password%}", resolve(user["credentials"]["password"]))
        )
        login = self.send("POST", params["loginRequestUrl"], user=name, body=body)
        for header in login["response"]["headers"]:
            if header["name"].lower() == "set-cookie":
                self.sessions[name] = header["value"].split(";", 1)[0]

    def passive(self, message: dict) -> None:
        names = {h["name"].lower() for h in message["response"]["headers"]}
        if "x-content-type-options" not in names:
            self.alert("10021", "X-Content-Type-Options Header Missing", "1", message)

    def alert(self, plugin, name, risk, message, *, param="", attack="", evidence=""):
        request, response = message["request"], message["response"]
        key = (plugin, request["url"].split("?")[0], request["method"], param)
        self.alerts.setdefault(
            key,
            {
                "pluginid": plugin,
                "alertRef": plugin,
                "alert": name,
                "name": name,
                "riskcode": risk,
                "confidence": "2",
                "instances": [
                    {
                        "uri": request["url"],
                        "method": request["method"],
                        "param": param,
                        "attack": attack,
                        "evidence": evidence,
                        "request-header": "\r\n".join(
                            [f"{request['method']} {request['url']} HTTP/1.1"]
                            + [f"{h['name']}: {h['value']}" for h in request["headers"]]
                        ),
                        "request-body": request["postData"]["text"],
                        "response-header": "\r\n".join(
                            [f"HTTP/1.1 {response['status']}"]
                            + [
                                f"{h['name']}: {h['value']}"
                                for h in response["headers"]
                            ]
                        ),
                        "response-body": response["content"]["text"],
                    }
                ],
            },
        )

    def spider(self, params: dict) -> None:
        user = params.get("user")
        queue = [(url, 0) for url in self.context["urls"]]
        seen: set[str] = set()
        while queue and len(seen) < params.get("maxChildren", 50):
            url, depth = queue.pop(0)
            if (
                url in seen
                or not self.in_scope(url)
                or depth > params.get("maxDepth", 5)
            ):
                continue
            seen.add(url)
            message = self.send("GET", url, user=user)
            response = message["response"]
            if params.get("postForm"):
                for action in re.findall(
                    r'<form method="POST" action="([^"]+)"', response["content"]["text"]
                ):
                    self.send("POST", urljoin(url, action), user=user, body="q=1")
            links = re.findall(r'href="([^"]+)"', response["content"]["text"])
            links += [
                h["value"]
                for h in response["headers"]
                if h["name"].lower() == "location"
            ]
            queue.extend((urljoin(url, link), depth + 1) for link in links)
        self.sites.extend(sorted(seen))

    def active(self, params: dict, strength: str) -> None:
        user = params.get("user")
        attacks = {"Low": 1, "Medium": 2, "High": 3}[strength]
        for url in list(self.sites):
            parts = urlsplit(url)
            for name, _ in parse_qsl(parts.query):
                for _ in range(attacks):
                    query = urlencode({name: PAYLOAD})
                    attacked = parts._replace(query=query).geturl()
                    message = self.send("GET", attacked, user=user)
                    if PAYLOAD in message["response"]["content"]["text"]:
                        self.alert(
                            "40012",
                            "Cross Site Scripting (Reflected)",
                            "3",
                            message,
                            param=name,
                            attack=PAYLOAD,
                            evidence=PAYLOAD,
                        )
                        break

    def openapi(self, params: dict) -> None:
        with open(local(params["apiFile"])) as handle:
            spec = json.load(handle)
        for path, operations in spec["paths"].items():
            if "get" in operations:
                url = params["targetUrl"].rstrip("/") + path + "?name=fixture"
                self.send("GET", url, user=params.get("user"))
                self.sites.append(url)

    def report(self, params: dict) -> None:
        origin = self.context["urls"][0].split("/", 3)
        site = "/".join(origin[:3])
        body = {
            "@programName": "ZAP",
            "@version": "2.17.0",
            "site": [{"@name": site, "alerts": list(self.alerts.values())}],
            "afPlanErrors": [],
            "afPlanWarns": ["fixture warning"] if fault() == "warning" else [],
        }
        text = json.dumps(body)
        if fault() == "malformed" and params["reportFile"] == "report.json":
            text = text[: len(text) // 2]
        path = os.path.join(local(params["reportDir"]), params["reportFile"])
        with open(path, "w") as handle:
            handle.write(text)

    def export(self, params: dict) -> None:
        entries = [m for m in self.messages if self.in_scope(m["request"]["url"])]
        with open(local(params["fileName"]), "w") as handle:
            json.dump({"log": {"version": "1.2", "entries": entries}}, handle)

    def run(self) -> int:
        for job in self.plan["jobs"]:
            kind, params = job["type"], job.get("parameters", {})
            if kind == "activeScan" and fault() == "cancel":
                return 143  # terminated after the passive phase
            if kind == "requestor":
                for request in job["requests"]:
                    self.send(
                        request["method"], request["url"], user=params.get("user")
                    )
            elif kind == "spider":
                self.spider(params)
            elif kind == "openapi":
                self.openapi(params)
            elif kind == "activeScan":
                self.active(params, job["policyDefinition"]["defaultStrength"])
            elif kind == "report":
                self.report(params)
            elif kind == "export":
                self.export(params)
        return 2 if fault() == "warning" else 0


def fault() -> str:
    return os.environ.get("FIXTURE_ZAP_FAULT", "")


def main(argv: list[str]) -> int:
    if argv[:2] != ["-cmd", "-autorun"]:
        return 64
    config = dict(
        argv[i + 1].split("=", 1)
        for i in range(3, len(argv), 2)
        if argv[i] == "-config"
    )
    proxy = None
    if config.get("network.connection.httpProxy.enabled") == "true":
        named = (
            f"{config['network.connection.httpProxy.host']}:"
            f"{config['network.connection.httpProxy.port']}"
        )
        # The fixture gateway listens on an ephemeral loopback port.
        mapped = dict(
            item.split("=", 1) for item in os.environ["FIXTURE_PROXY_MAP"].split(",")
        )
        host, _, port = mapped.get(named, named).partition(":")
        proxy = (host, int(port))
    rule = "network.ratelimit.rules.rule(0)."
    rate = None
    if config.get(rule + "enabled") == "true" and config.get(rule + "matchStr") == ".*":
        rate = int(config[rule + "reqsPerSec"])
    with open(local(argv[2])) as handle:
        plan = json.load(handle)
    return Zap(plan, proxy, rate).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
