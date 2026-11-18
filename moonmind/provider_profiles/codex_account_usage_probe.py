"""Bounded Codex OAuth account-usage probe (MoonLadderStudios/MoonMind#5364).

MoonMind is not installed in the Omnigent host image, so this file is shipped
as source text to ``python3 -c`` there and uses only the standard library. It
runs in the short-lived usage probe container under the exclusive
``usage_observation`` grant, with the enrolled Codex home mounted writable at
the path given on the command line.

The installed ``codex app-server`` does all provider I/O over stdio. After
``initialize`` the probe sends ``account/read`` (``refreshToken: false``),
``account/rateLimits/read`` and a second ``account/read``. A changed account
between the two account reads means the pair straddled a login or workspace
switch, so nothing is reported. ``excludeResetCreditDetails`` skips the
reset-credit inventory; a protocol that rejects it is asked again without it.
No turn is started and no reset is redeemed.

Codex renews an expired access token itself when its app-server starts and
rotates the refresh token into ``auth.json``; the home is writable so that
rotation persists. ``recover`` runs only after ``read`` reported a rejected
token: it asks Codex for one forced renewal (``refreshToken: true``) and then
repeats the read once.

The output is one JSON object of allowlisted fields. Tokens, provider error
text and raw bodies are never printed.
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import sys
import time

REQUEST_TIMEOUT_SECONDS = 20.0
MAX_LINE_BYTES = 4 * 1024 * 1024
_STOP_GRACE_SECONDS = 2.0
_MAX_BUCKETS = 16
#: Inherited credentials would make Codex read a different login.
_ENV_DROP = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN")
_UNAUTHORIZED = re.compile(r"(?<![0-9])401(?![0-9])")
_AUTH_MARKERS = (
    "unauthorized",
    "token expired",
    "expired token",
    "invalid token",
    "could not be refreshed",
    "not logged in",
    "authentication failed",
)
_FORBIDDEN = re.compile(r"(?<![0-9])403(?![0-9])|forbidden")
_RATE_LIMITED = re.compile(r"(?<![0-9])429(?![0-9])|too many requests")
_NOT_CHATGPT = re.compile(r"chatgpt authentication required")
_VERSION = re.compile(r"^[^/\s]{1,64}/([0-9][0-9A-Za-z.+-]{0,31})")
_BUCKET_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,48}$")


class ProbeFailure(Exception):
    def __init__(self, outcome):
        super().__init__(outcome.get("failure") or outcome.get("outcome"))
        self.outcome = outcome


def _failed(code):
    return ProbeFailure({"outcome": "failed", "failure": code})


def _text(value, limit=128):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or not text.isprintable():
        return None
    return text


def _scalar(value):
    """Keep a JSON number or null; anything else is marked unreadable."""

    if value is None or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ):
        return value
    return "invalid"


def _window(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return "invalid"
    return {
        "used_percent": _scalar(value.get("usedPercent")),
        "window_duration_mins": _scalar(value.get("windowDurationMins")),
        "resets_at": _scalar(value.get("resetsAt")),
    }


def _bucket(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return "invalid"
    return {
        "limit_id": _text(value.get("limitId"), 48),
        "limit_name": _text(value.get("limitName")),
        "model": _text(value.get("normalModelSlug")),
        "primary": _window(value.get("primary")),
        "secondary": _window(value.get("secondary")),
    }


def _account_identity(result):
    """The account fields that must not change across the read pair."""

    if not isinstance(result, dict):
        return None
    account = result.get("account")
    routing = result.get("workspaceRouting")
    return {
        "account": (
            {
                "type": _text(account.get("type"), 32),
                "email": _text(account.get("email"), 320),
                "plan_type": _text(account.get("planType"), 64),
                "workspace_name": _text(account.get("workspaceName")),
            }
            if isinstance(account, dict)
            else None
        ),
        "routing_account_id": (
            _text(routing.get("chatgptAccountId"))
            if isinstance(routing, dict)
            else None
        ),
        "user_id": _text(result.get("userId")),
    }


def project(account_before, rate_limits, account_after, *, observed_at, codex_version):
    """Allowlisted projection of one read pair, or ``None`` if it straddled.

    ``account_before``/``account_after`` are ``account/read`` results (``None``
    when that read failed) and ``rate_limits`` the ``account/rateLimits/read``
    result. Account display fields are kept only when both account reads
    succeeded and agree. The Omnigent native Codex forwarder publishes the
    same shape, so MoonMind parses both with one translator.
    """

    before, after = _account_identity(account_before), _account_identity(account_after)
    if before is not None and after is not None and before != after:
        return None
    identity = after if before is not None else None
    by_limit = rate_limits.get("rateLimitsByLimitId")
    buckets = None
    if isinstance(by_limit, dict):
        buckets = {
            key: _bucket(value)
            for key, value in list(by_limit.items())[:_MAX_BUCKETS]
            if isinstance(key, str) and _BUCKET_ID.fullmatch(key)
        }
    elif by_limit is not None:
        buckets = "invalid"
    return {
        "observed_at": observed_at,
        "codex_version": codex_version,
        "account": identity["account"] if identity else None,
        "routing_account_id": identity["routing_account_id"] if identity else None,
        "account_id": _text(rate_limits.get("accountId")),
        "rate_limits": _bucket(rate_limits.get("rateLimits")),
        "rate_limits_by_limit_id": buckets,
    }


def _message(error):
    return str(error.get("message", "")).lower() if isinstance(error, dict) else ""


def classify_error(error, *, recovering):
    """Map an app-server error to a typed outcome; its text is never kept."""

    message = _message(error)
    if (
        (isinstance(error, dict) and error.get("code") == 401)
        or _UNAUTHORIZED.search(message)
        or any(marker in message for marker in _AUTH_MARKERS)
    ):
        if recovering:
            return {"outcome": "failed", "failure": "credential_refresh_unavailable"}
        return {"outcome": "recovery_required"}
    if _RATE_LIMITED.search(message):
        return {"outcome": "failed", "failure": "rate_limited"}
    if _FORBIDDEN.search(message) or _NOT_CHATGPT.search(message):
        return {"outcome": "failed", "failure": "usage_access_missing"}
    return {"outcome": "failed", "failure": "provider_unavailable"}


def _rejects_parameter(error, name):
    return isinstance(error, dict) and (
        error.get("code") == -32602
        or (
            error.get("code") == -32600 and f"unknown field `{name}`" in _message(error)
        )
    )


class AppServer:
    """One stdio ``codex app-server`` against the enrolled home."""

    def __init__(self, home, command=("codex",)):
        env = {key: value for key, value in os.environ.items() if key not in _ENV_DROP}
        env["CODEX_HOME"] = home
        self._process = subprocess.Popen(
            [*command, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        self._next_id = 1
        self._buffer = b""

    def _send(self, message):
        try:
            self._process.stdin.write((json.dumps(message) + "\n").encode())
            self._process.stdin.flush()
        except OSError:
            # The app-server exited before reading the request.
            raise _failed("provider_unavailable") from None

    def _line(self, deadline):
        stdout = self._process.stdout
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _failed("timeout")
            ready, _, _ = select.select([stdout], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(stdout.fileno(), 65536)
            if not chunk:
                raise _failed("provider_unavailable")
            self._buffer += chunk
            if len(self._buffer) > MAX_LINE_BYTES:
                raise _failed("malformed_response")
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line

    def request(self, method, params):
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        while True:
            try:
                message = json.loads(self._line(deadline))
            except ValueError:
                continue
            if (
                isinstance(message, dict)
                and message.get("id") == request_id
                and "method" not in message
            ):
                return message

    def notify(self, method):
        self._send({"method": method})

    def stop(self):
        process = self._process
        for sig, wait in (
            (signal.SIGTERM, _STOP_GRACE_SECONDS),
            (signal.SIGKILL, None),
        ):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue


def _account(server, home, *, recovering):
    """``account/read`` result, or ``None`` when only identity is unavailable."""

    response = server.request("account/read", {"refreshToken": False})
    error = response.get("error")
    if error is not None:
        outcome = classify_error(error, recovering=recovering)
        if outcome.get("failure") == "provider_unavailable":
            # Missing email or workspace never discards valid usage.
            return None
        raise ProbeFailure(outcome)
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    account = result.get("account")
    if account is None:
        # Codex found no usable login, even after any renewal it attempted.
        if not os.path.isfile(os.path.join(home, "auth.json")):
            raise _failed("reconnect_required")
        if recovering:
            raise _failed("credential_refresh_unavailable")
        raise ProbeFailure({"outcome": "recovery_required"})
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise _failed("usage_access_missing")
    return result


def _rate_limits(server, *, recovering):
    params = {"excludeResetCreditDetails": True}
    response = server.request("account/rateLimits/read", params)
    if _rejects_parameter(response.get("error"), "excludeResetCreditDetails"):
        response = server.request("account/rateLimits/read", {})
    error = response.get("error")
    if error is not None:
        raise ProbeFailure(classify_error(error, recovering=recovering))
    result = response.get("result")
    if not isinstance(result, dict):
        raise _failed("malformed_response")
    return result


def observe(home, *, recovering, command=("codex",), clock=time.time):
    server = AppServer(home, command)
    try:
        initialized = server.request(
            "initialize",
            {
                "clientInfo": {"name": "moonmind-usage", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        result = initialized.get("result")
        if not isinstance(result, dict):
            raise _failed("provider_unavailable")
        match = _VERSION.match(str(result.get("userAgent") or ""))
        server.notify("initialized")
        if recovering:
            # One native renewal through Codex's own refresh flow.
            server.request("account/read", {"refreshToken": True})
        before = _account(server, home, recovering=recovering)
        limits = _rate_limits(server, recovering=recovering)
        observed_at = clock()
        after = _account(server, home, recovering=recovering)
    finally:
        server.stop()
    projected = project(
        before,
        limits,
        after,
        observed_at=observed_at,
        codex_version=match.group(1) if match else None,
    )
    if projected is None:
        # The account changed between the two reads.
        raise _failed("provider_unavailable")
    if projected["rate_limits"] == "invalid" or (
        projected["rate_limits"] is None and not projected["rate_limits_by_limit_id"]
    ):
        raise _failed("malformed_response")
    return {"outcome": "observed", "usage": projected}


def main(argv):
    if len(argv) != 2 or argv[0] not in ("read", "recover"):
        print(json.dumps({"v": 1, "outcome": "failed", "failure": "internal_error"}))
        return 2
    mode, home = argv
    try:
        result = observe(home, recovering=mode == "recover")
    except ProbeFailure as failure:
        result = failure.outcome
    except Exception:
        result = {"outcome": "failed", "failure": "internal_error"}
    print(json.dumps({"v": 1, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
