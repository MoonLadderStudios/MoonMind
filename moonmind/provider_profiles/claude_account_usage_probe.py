"""Bounded Claude OAuth account-usage probe (MoonLadderStudios/MoonMind#5365).

MoonMind is not installed in the Omnigent host image, so this file is shipped
as source text to ``python3 -c`` there. It uses only the standard library and
the image's ``httpx``. It runs in the short-lived usage probe container under
the exclusive ``usage_observation`` grant, with the enrolled Claude home
mounted at the path given on the command line.

``read`` reads the stored access token once. It makes one
``/api/oauth/usage`` request and, when that succeeds, one
``/api/oauth/profile`` request with the same token, so identity and usage come
from one credential context.

``recover`` runs only when that token has expired. It starts the installed
Claude Code CLI on the writable home and asks it ``initialize`` and
``get_usage`` over the SDK control protocol. It never sends a prompt. The CLI
renews the token through its own lifecycle: a lock in the config directory, a
compare-and-set write of the rotated tokens, and clearing a refresh token the
provider rejected. This script never refreshes a token itself. After a renewal
it repeats ``read`` once.

The output is one JSON object of allowlisted fields. Tokens, headers, and raw
provider bodies are never printed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

API_ORIGIN = "https://api.anthropic.com"
USAGE_PATH = "/api/oauth/usage"
PROFILE_PATH = "/api/oauth/profile"
#: Compatibility header the internal OAuth routes require; tested here only.
OAUTH_BETA = "oauth-2025-04-20"
PROFILE_SCOPE = "user:profile"
REQUEST_TIMEOUT_SECONDS = 15.0
MAX_BODY_BYTES = 262_144
#: A token this close to expiry is treated as expired, so recovery runs first.
EXPIRY_MARGIN_SECONDS = 60
CLI_ANSWER_SECONDS = 45.0
_CLI_EXIT_GRACE_SECONDS = 3.0
_CLI_ARGS = (
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--tools",
    "",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
    "--settings",
    '{"disableAllHooks":true}',
    "--setting-sources",
    "",
    "--disable-slash-commands",
    "--no-session-persistence",
)
#: Inherited credentials would make the CLI use a different login.
_CLI_ENV_DROP = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
)
_KEY = re.compile(r"^[a-z0-9_]{1,48}$")
_MAX_WINDOWS = 32
_MAX_LIMITS = 64


def _text(value, limit=64):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or not text.isprintable():
        return None
    return text


def _scalar(value):
    """Keep a bounded JSON scalar; anything else is marked unreadable."""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and len(value) <= 64 and value.isprintable():
        return value
    return "invalid"


def project_usage(body):
    """Reduce a usage body to window fields; MoonMind decides their meaning."""

    windows = {}
    for key, value in body.items():
        if len(windows) >= _MAX_WINDOWS:
            break
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            continue
        if value is None:
            # A known window answered with null is "not reported".
            windows[key] = None
        elif isinstance(value, dict) and (
            "utilization" in value or "resets_at" in value
        ):
            windows[key] = {
                "utilization": _scalar(value.get("utilization")),
                "resets_at": _scalar(value.get("resets_at")),
            }
    limits = None
    raw = body.get("limits")
    if isinstance(raw, list):
        limits = []
        for entry in raw[:_MAX_LIMITS]:
            if not isinstance(entry, dict):
                continue
            scope = entry.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            limits.append(
                {
                    "kind": _text(entry.get("kind")),
                    "group": _text(entry.get("group")),
                    "percent": _scalar(entry.get("percent")),
                    "resets_at": _scalar(entry.get("resets_at")),
                    "model": (
                        _text(model.get("display_name"))
                        if isinstance(model, dict)
                        else None
                    ),
                    "scoped": scope is not None,
                }
            )
    return {"windows": windows, "limits": limits}


def project_profile(body):
    account = body.get("account")
    organization = body.get("organization")
    return {
        "account": (
            {
                "uuid": _text(account.get("uuid"), 128),
                "email": _text(account.get("email"), 320),
            }
            if isinstance(account, dict)
            else None
        ),
        "organization": (
            {
                "uuid": _text(organization.get("uuid"), 128),
                "name": _text(organization.get("name"), 128),
            }
            if isinstance(organization, dict)
            else None
        ),
    }


def load_oauth(home):
    try:
        with open(os.path.join(home, ".credentials.json"), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
    return oauth if isinstance(oauth, dict) else None


def token_state(oauth, now):
    """``missing``, ``no_scope``, ``expired`` or ``valid``; reads no secret out."""

    token = oauth.get("accessToken") if oauth else None
    if not isinstance(token, str) or not token:
        return "missing"
    scopes = oauth.get("scopes")
    if isinstance(scopes, list) and PROFILE_SCOPE not in scopes:
        # Inference-only (setup-token style) credentials cannot read usage;
        # renewing them would not add the scope.
        return "no_scope"
    expires = oauth.get("expiresAt")
    if (
        isinstance(expires, (int, float))
        and not isinstance(expires, bool)
        and expires / 1000 <= now + EXPIRY_MARGIN_SECONDS
    ):
        return "expired"
    return "valid"


def _retry_after(response):
    return _text(response.headers.get("retry-after"))


def _get(client, path, token):
    import httpx

    try:
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException:
        return "timeout", None, None
    except httpx.HTTPError:
        return "provider_unavailable", None, None
    status = response.status_code
    if status == 401:
        return "recovery_required", None, None
    if status == 403:
        return "usage_access_missing", None, None
    if status == 429:
        return "rate_limited", None, _retry_after(response)
    if status != 200:
        # Redirects are not followed: the provider host is fixed.
        return "provider_unavailable", None, None
    if len(response.content) > MAX_BODY_BYTES:
        return "malformed_response", None, None
    try:
        body = response.json()
    except ValueError:
        return "malformed_response", None, None
    if not isinstance(body, dict):
        return "malformed_response", None, None
    return "ok", body, None


def read(home, *, transport=None, clock=time.time):
    import httpx

    oauth = load_oauth(home)
    state = token_state(oauth, clock())
    if state == "missing":
        return {"outcome": "failed", "failure": "reconnect_required"}
    if state == "no_scope":
        return {"outcome": "failed", "failure": "usage_access_missing"}
    if state == "expired":
        return {"outcome": "recovery_required"}
    token = oauth["accessToken"]
    with httpx.Client(
        base_url=API_ORIGIN,
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"Accept": "application/json", "anthropic-beta": OAUTH_BETA},
        transport=transport,
        trust_env=transport is None,
    ) as client:
        observed_at = clock()
        result, body, retry_after = _get(client, USAGE_PATH, token)
        if result == "recovery_required":
            return {"outcome": "recovery_required"}
        if result != "ok":
            return {"outcome": "failed", "failure": result, "retryAfter": retry_after}
        usage = project_usage(body)
        # A failed identity read keeps the valid usage it accompanies.
        profile_result, profile_body, _ = _get(client, PROFILE_PATH, token)
    return {
        "outcome": "observed",
        "observedAt": observed_at,
        "usage": usage,
        "profile": project_profile(profile_body) if profile_result == "ok" else None,
    }


def _cli_env(home):
    env = {key: value for key, value in os.environ.items() if key not in _CLI_ENV_DROP}
    scratch = tempfile.mkdtemp(prefix="usage-recovery-", dir=env.get("HOME") or None)
    env.update(
        CLAUDE_CONFIG_DIR=home,
        TMPDIR=scratch,
        DISABLE_AUTOUPDATER="1",
        DISABLE_TELEMETRY="1",
        DISABLE_ERROR_REPORTING="1",
    )
    return env, scratch


def ask_cli(command, home, *, timeout=None):
    """Ask the CLI ``initialize`` then ``get_usage``; never a prompt.

    Returns ``answered``, ``unsupported`` (it rejected the control request)
    or ``no_answer`` (it exited or timed out). The CLI's process group is
    always killed before returning, so no writer outlives the probe.
    """

    env, scratch = _cli_env(home)
    deadline = time.monotonic() + (CLI_ANSWER_SECONDS if timeout is None else timeout)
    process = subprocess.Popen(
        [command, *_CLI_ARGS],
        cwd=scratch,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    outcome = "no_answer"
    try:
        for request_id, request in (
            ("usage-init", {"subtype": "initialize", "hooks": None}),
            ("usage-read", {"subtype": "get_usage", "skip_behaviors": True}),
        ):
            line = json.dumps(
                {
                    "type": "control_request",
                    "request_id": request_id,
                    "request": request,
                }
            )
            process.stdin.write(line.encode() + b"\n")
            process.stdin.flush()
            response = _await_response(process, request_id, deadline)
            if response is None:
                outcome = "no_answer"
                break
            if response.get("subtype") != "success":
                outcome = "unsupported"
                break
            outcome = "answered"
    except (OSError, ValueError):
        outcome = "no_answer"
    finally:
        _stop(process, graceful=outcome == "answered")
        shutil.rmtree(scratch, ignore_errors=True)
    return outcome


def _await_response(process, request_id, deadline):
    import selectors

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    buffer = b""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                return None
            chunk = os.read(process.stdout.fileno(), 65_536)
            if not chunk:
                return None
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                with contextlib.suppress(ValueError):
                    message = json.loads(line)
                    response = (
                        message.get("response") if isinstance(message, dict) else None
                    )
                    if (
                        message.get("type") == "control_response"
                        and isinstance(response, dict)
                        and response.get("request_id") == request_id
                    ):
                        return response
            if len(buffer) > 16 * 1024 * 1024:
                return None
    finally:
        selector.close()


def _stop(process, *, graceful):
    with contextlib.suppress(OSError):
        process.stdin.close()
    if graceful:
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=_CLI_EXIT_GRACE_SECONDS)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)


def recover(home, *, transport=None, clock=time.time, command=None):
    before = load_oauth(home)
    state = token_state(before, clock())
    if state in ("missing", "no_scope"):
        return read(home, transport=transport, clock=clock)
    command = command or shutil.which("claude")
    if command is None:
        return {
            "outcome": "failed",
            "failure": "credential_recovery_unsupported",
            "recovery": "unsupported",
        }
    answer = ask_cli(command, home)
    after = load_oauth(home) or {}
    if after.get("refreshToken") == "" or token_state(after, clock()) == "missing":
        # The CLI clears a refresh token only after the provider rejected it.
        return {
            "outcome": "failed",
            "failure": "reconnect_required",
            "recovery": "rejected",
        }
    renewed = token_state(after, clock()) == "valid" and after.get("accessToken") != (
        before or {}
    ).get("accessToken")
    if renewed:
        result = read(home, transport=transport, clock=clock)
        if result["outcome"] == "recovery_required":
            # One renewal per attempt; a rejected new token is not retried.
            result = {"outcome": "failed", "failure": "credential_refresh_unavailable"}
        return {**result, "recovery": "renewed"}
    if answer == "unsupported":
        return {
            "outcome": "failed",
            "failure": "credential_recovery_unsupported",
            "recovery": "unsupported",
        }
    # Not renewed or uncertain (no answer, lock held, network): the next
    # attempt re-reads whatever the CLI persisted instead of retrying now.
    return {
        "outcome": "failed",
        "failure": "credential_refresh_unavailable",
        "recovery": "not_renewed" if answer == "answered" else "uncertain",
    }


def main(argv):
    if len(argv) != 2 or argv[0] not in ("read", "recover"):
        print(json.dumps({"v": 1, "outcome": "failed", "failure": "internal_error"}))
        return 2
    mode, home = argv
    try:
        result = read(home) if mode == "read" else recover(home)
    except Exception:
        result = {"outcome": "failed", "failure": "internal_error"}
    print(json.dumps({"v": 1, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
