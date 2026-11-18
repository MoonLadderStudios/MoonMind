"""Claude OAuth account-usage adapter (MoonLadderStudios/MoonMind#5365).

Recorded, sanitized fixtures and mock transports only: no credentials, no
provider calls, no inference.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from moonmind.provider_profiles import claude_account_usage as claude
from moonmind.provider_profiles import claude_account_usage_probe as probe
from moonmind.provider_profiles.account_usage import (
    AccountUsageFailureCode,
    UsageWindowPeriod,
    UsageWindowScope,
)
from moonmind.provider_profiles.usage_collection import (
    IdleProbeContext,
    UsageCollectionTarget,
    default_usage_collectors,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "provider" / "claude_oauth"
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
ACCESS = "sk-ant-oat01-ACCESSACCESSACCESS"
REFRESH = "sk-ant-ort01-REFRESHREFRESHREFRESH"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _projected(body: dict) -> dict:
    return probe.project_usage(body)


def _usage(body: dict):
    return claude.usage_from_oauth_usage(_projected(body), T0)


def _windows(body: dict) -> dict:
    return {window.key: window for window in _usage(body).windows}


# -- HTTP usage normalization ---------------------------------------------------


def test_recorded_usage_keeps_general_short_and_model_scopes_separate() -> None:
    usage = _usage(_fixture("usage.json"))
    windows = {window.key: window for window in usage.windows}

    assert usage.completeness.value == "full"
    assert usage.primary_window_key == "seven_day"
    weekly = windows["seven_day"]
    assert (weekly.period, weekly.scope, weekly.used_percent) == (
        UsageWindowPeriod.WEEKLY,
        UsageWindowScope.GENERAL,
        42.0,
    )
    assert weekly.reset_at == datetime(2026, 11, 20, 9, tzinfo=UTC)
    assert weekly.source == "claude_oauth_usage"
    assert (windows["five_hour"].period, windows["five_hour"].used_percent) == (
        UsageWindowPeriod.SHORT,
        16.0,
    )
    # Legacy seven_day_opus and the weekly "Opus" limits[] entry are one pool.
    assert windows["seven_day_opus"].model_label == "Opus"
    assert windows["seven_day_opus"].used_percent == 1.0
    # A model pool only limits[] reports, kept although is_active is false.
    assert windows["seven_day_fable"].scope is UsageWindowScope.MODEL
    assert windows["seven_day_fable"].used_percent == 3.0
    # Unknown keys and kinds are never classified as weekly.
    assert set(windows) == {
        "seven_day",
        "five_hour",
        "seven_day_opus",
        "seven_day_fable",
    }


@pytest.mark.parametrize("used", [0, 1, 1.0, 100, 250])
def test_utilization_is_a_percentage_not_a_fraction(used) -> None:
    window = _windows({"seven_day": {"utilization": used, "resets_at": None}})[
        "seven_day"
    ]
    assert window.used_percent == float(used)
    assert window.reset_at is None


@pytest.mark.parametrize(
    "value",
    [True, "abc", -1, "1e3", float("nan")],
)
def test_unreadable_percentages_fail_the_reading(value) -> None:
    with pytest.raises(claude._Malformed):
        _usage({"seven_day": {"utilization": value, "resets_at": None}})


@pytest.mark.parametrize("reset", ["2026-11-20T09:00:00", "tomorrow", 1.5e15, True])
def test_unreadable_resets_fail_the_reading(reset) -> None:
    with pytest.raises(claude._Malformed):
        _usage({"seven_day": {"utilization": 3, "resets_at": reset}})


def test_percentage_and_reset_stay_independently_nullable() -> None:
    windows = _windows(
        {
            "seven_day": {"utilization": None, "resets_at": "2026-11-20T09:00:00Z"},
            "five_hour": {"utilization": 5},
            "seven_day_sonnet": {"utilization": None, "resets_at": None},
        }
    )
    assert windows["seven_day"].used_percent is None
    assert windows["seven_day"].reset_at == datetime(2026, 11, 20, 9, tzinfo=UTC)
    assert windows["five_hour"].reset_at is None
    # A window with neither value is not reported, not fabricated.
    assert "seven_day_sonnet" not in windows


def test_absent_windows_are_not_reported_but_a_changed_schema_is_malformed() -> None:
    for body in ({"five_hour": None, "seven_day": None}, {"limits": []}):
        empty = _usage(body)
        assert empty.windows == () and empty.primary_window_key is None
    with pytest.raises(claude._Malformed):
        _usage({"weekly_allowance": {"utilization": 3, "resets_at": None}})


def test_limits_only_responses_use_the_same_keys_as_legacy_and_native() -> None:
    windows = _windows(
        {
            "limits": [
                {
                    "kind": "weekly",
                    "group": "subscription",
                    "percent": 9,
                    "scope": None,
                },
                {"kind": "session", "group": "subscription", "percent": 2},
                {
                    "kind": "session",
                    "group": "subscription",
                    "percent": 4,
                    "scope": {"model": {"display_name": "Opus"}},
                },
                {"kind": "weekly", "group": "spend", "percent": 50, "scope": None},
                {"kind": "weekly", "percent": 70, "scope": {"surface": "web"}},
                {"kind": "daily", "percent": 80, "scope": None},
            ]
        }
    )
    assert set(windows) == {"seven_day", "five_hour", "five_hour_opus"}
    assert windows["seven_day"].used_percent == 9.0
    model_short = windows["five_hour_opus"]
    assert (model_short.period, model_short.scope, model_short.model_label) == (
        UsageWindowPeriod.SHORT,
        UsageWindowScope.MODEL,
        "Opus",
    )


def test_legacy_value_wins_over_its_limits_duplicate() -> None:
    windows = _windows(
        {
            "seven_day": {"utilization": 40, "resets_at": None},
            "limits": [
                {"kind": "weekly", "group": "subscription", "percent": 41},
                {"kind": "weekly", "group": "subscription", "percent": "bad"},
            ],
        }
    )
    assert windows["seven_day"].used_percent == 40.0


def test_oauth_apps_pool_is_reported_as_another_limit() -> None:
    window = _windows({"seven_day_oauth_apps": {"utilization": 12, "resets_at": None}})[
        "seven_day_oauth_apps"
    ]
    assert window.period is UsageWindowPeriod.OTHER
    assert window.scope is UsageWindowScope.GENERAL


# -- identity -------------------------------------------------------------------


def test_profile_identity_binds_account_and_workspace() -> None:
    account_id, workspace_id, identity = claude.identity_from_oauth_profile(
        probe.project_profile(_fixture("profile.json")), T0
    )
    assert account_id == "6f1c2a9e-0000-4000-8000-000000000001"
    assert workspace_id == "0a7d3b10-0000-4000-8000-0000000000aa"
    assert identity.email == "person@example.com"
    assert identity.display_name == "Example Workspace"
    assert identity.verified is True
    assert identity.source == "claude_oauth_profile"


def test_profile_without_account_id_is_display_only() -> None:
    account_id, workspace_id, identity = claude.identity_from_oauth_profile(
        {"account": {"uuid": None, "email": "not an email"}, "organization": None},
        T0,
    )
    assert (account_id, workspace_id, identity) == (None, None, None)
    _, _, identity = claude.identity_from_oauth_profile(
        {"account": {"email": "person@example.com"}}, T0
    )
    assert identity.email == "person@example.com" and identity.verified is False


# -- native status line ---------------------------------------------------------


def test_status_line_epoch_seconds_normalize_per_window() -> None:
    weekly_at, short_at = T0.timestamp() - 600, T0.timestamp()
    report = claude.status_line_report(
        {
            "seven_day": {
                "used_percentage": 42,
                "resets_at": 1763629200,
                "observed_at": weekly_at,
            },
            "five_hour": {
                "used_percentage": 0,
                "resets_at": None,
                "observed_at": short_at,
            },
            "claude_code_version": "2.1.281",
        }
    )
    usage = report.usage
    windows = {window.key: window for window in usage.windows}
    assert usage.completeness.value == "partial"
    assert usage.observed_at == T0
    assert windows["seven_day"].reset_at == datetime.fromtimestamp(1763629200, UTC)
    assert windows["seven_day"].observed_at == T0 - timedelta(minutes=10)
    assert windows["five_hour"].used_percent == 0.0
    assert windows["seven_day"].source == "claude_status_line"
    assert windows["seven_day"].source_version == "2.1.281"


def test_status_line_skips_unreadable_windows_and_keeps_the_rest() -> None:
    report = claude.status_line_report(
        {
            "seven_day": {"used_percentage": True, "observed_at": T0.timestamp()},
            "five_hour": {"used_percentage": 7, "observed_at": T0.timestamp()},
            "seven_day_opus": {"used_percentage": 1, "observed_at": T0.timestamp()},
        }
    )
    assert [window.key for window in report.usage.windows] == ["five_hour"]
    # A window without its capture time cannot claim freshness.
    assert claude.status_line_report({"seven_day": {"used_percentage": 4}}) is None


# -- probe output ---------------------------------------------------------------


def _probe_stdout(**payload) -> bytes:
    return json.dumps({"v": 1, **payload}).encode()


def test_observed_probe_output_reports_usage_and_identity() -> None:
    result = claude.parse_probe_output(
        0,
        _probe_stdout(
            outcome="observed",
            observedAt=T0.timestamp(),
            usage=_projected(_fixture("usage.json")),
            profile=probe.project_profile(_fixture("profile.json")),
        ),
    )
    report = result.report
    assert report.account_id == "6f1c2a9e-0000-4000-8000-000000000001"
    assert report.identity.email == "person@example.com"
    assert report.usage.primary_window_key == "seven_day"


def test_failed_identity_read_keeps_valid_usage() -> None:
    result = claude.parse_probe_output(
        0,
        _probe_stdout(
            outcome="observed",
            observedAt=T0.timestamp(),
            usage=_projected({"seven_day": {"utilization": 5, "resets_at": None}}),
            profile=None,
        ),
    )
    assert result.report.identity is None and result.report.account_id is None
    assert result.report.usage.windows[0].used_percent == 5.0


@pytest.mark.parametrize(
    ("exit_code", "stdout", "failure"),
    [
        (
            0,
            _probe_stdout(outcome="failed", failure="usage_access_missing"),
            "usage_access_missing",
        ),
        (0, _probe_stdout(outcome="failed", failure="no_such_code"), "internal_error"),
        (
            0,
            _probe_stdout(
                outcome="observed", observedAt=1.0, usage={"windows": {"x_y": {}}}
            ),
            "malformed_response",
        ),
        (
            0,
            _probe_stdout(outcome="observed", observedAt=None, usage={"windows": {}}),
            "malformed_response",
        ),
        (1, b"Traceback (most recent call last): ...", "internal_error"),
        (137, b"", "timeout"),
    ],
)
def test_probe_failures_are_typed(exit_code, stdout, failure) -> None:
    result = claude.parse_probe_output(exit_code, stdout)
    assert result.report is None
    assert result.failure is AccountUsageFailureCode(failure)


def test_rate_limit_keeps_retry_after_and_expiry_requests_recovery() -> None:
    limited = claude.parse_probe_output(
        0, _probe_stdout(outcome="failed", failure="rate_limited", retryAfter="0")
    )
    assert limited.failure is AccountUsageFailureCode.RATE_LIMITED
    assert limited.retry_after == "0"
    assert claude.parse_probe_output(
        0, _probe_stdout(outcome="recovery_required")
    ).recovery_required


# -- adapter --------------------------------------------------------------------


def _context(home: str = "/home/app/.claude") -> IdleProbeContext:
    return IdleProbeContext(
        target=UsageCollectionTarget(
            profile_id="alpha",
            credential_generation=1,
            provider_id="anthropic",
            runtime_id="claude_code",
            attempt_id="attempt-1",
            deadline_at=T0,
        ),
        credential_home_path=home,
    )


@pytest.mark.asyncio
async def test_adapter_is_registered_for_anthropic_oauth_on_claude_code() -> None:
    registry = default_usage_collectors()
    adapter = registry.get("claude_code", "anthropic")
    assert isinstance(adapter, claude.ClaudeUsageCollector)
    assert registry.get("claude_code", "minimax") is None
    assert registry.get("codex_cli", "openai") is None
    # Active owners answer through native telemetry; nothing reads twice.
    assert await adapter.request_from_owner(object()) is None


def test_idle_read_is_read_only_and_only_recovery_writes() -> None:
    adapter = claude.ClaudeUsageCollector()
    read = adapter.idle_probe(_context())
    recover = adapter.recovery_probe(_context())
    assert read.argv[:2] == ("python3", "-c")
    assert read.argv[3:] == ("read", "/home/app/.claude")
    assert read.writes_credentials is False
    assert recover.argv[3:] == ("recover", "/home/app/.claude")
    assert recover.writes_credentials is True
    assert recover.timeout_seconds > read.timeout_seconds


# -- probe script: read ---------------------------------------------------------


def _home(tmp_path: Path, **oauth) -> str:
    home = tmp_path / "claude-home"
    home.mkdir(exist_ok=True)
    values = {
        "accessToken": ACCESS,
        "refreshToken": REFRESH,
        "expiresAt": int((time.time() + 3600) * 1000),
        "scopes": ["user:inference", "user:profile"],
        "subscriptionType": "max",
    }
    values.update(oauth)
    (home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": values}))
    return str(home)


class Provider:
    """Mock Anthropic routes; records every request it sees."""

    def __init__(self, usage=None, profile=None, token: str = ACCESS) -> None:
        self.requests: list[httpx.Request] = []
        self.usage = usage or (200, _fixture("usage.json"), {})
        self.profile = profile or (200, _fixture("profile.json"), {})
        self.token = token

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": {"type": "authentication_error"}})
        status, body, headers = (
            self.usage if request.url.path == probe.USAGE_PATH else self.profile
        )
        if isinstance(body, Exception):
            raise body
        if isinstance(body, bytes):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def test_read_uses_one_token_for_usage_and_profile_with_fixed_headers(
    tmp_path,
) -> None:
    provider = Provider()
    result = probe.read(_home(tmp_path), transport=provider.transport())

    assert result["outcome"] == "observed"
    usage_request, profile_request = provider.requests
    for request in provider.requests:
        assert request.url.host == "api.anthropic.com"
        assert request.url.scheme == "https"
        assert request.headers["authorization"] == f"Bearer {ACCESS}"
        assert request.headers["accept"] == "application/json"
        assert request.headers["anthropic-beta"] == "oauth-2025-04-20"
    assert (usage_request.url.path, profile_request.url.path) == (
        "/api/oauth/usage",
        "/api/oauth/profile",
    )
    output = json.dumps(result)
    assert ACCESS not in output and REFRESH not in output
    # Only allowlisted fields leave the probe.
    assert "full_name" not in output and "billing_type" not in output
    assert "amount_minor" not in output and "limit_dollars" not in output


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ((401, {}, {}), {"outcome": "recovery_required"}),
        (
            (403, {"error": {"type": "permission_error"}}, {}),
            {
                "outcome": "failed",
                "failure": "usage_access_missing",
                "retryAfter": None,
            },
        ),
        (
            (429, {}, {"retry-after": "0"}),
            {"outcome": "failed", "failure": "rate_limited", "retryAfter": "0"},
        ),
        (
            (500, {}, {}),
            {
                "outcome": "failed",
                "failure": "provider_unavailable",
                "retryAfter": None,
            },
        ),
        (
            (302, {}, {"location": "https://evil.example/usage"}),
            {
                "outcome": "failed",
                "failure": "provider_unavailable",
                "retryAfter": None,
            },
        ),
        (
            (200, b"<html>", {}),
            {"outcome": "failed", "failure": "malformed_response", "retryAfter": None},
        ),
        (
            (200, [1, 2], {}),
            {"outcome": "failed", "failure": "malformed_response", "retryAfter": None},
        ),
        (
            (200, httpx.ReadTimeout("slow"), {}),
            {"outcome": "failed", "failure": "timeout", "retryAfter": None},
        ),
        (
            (200, httpx.ConnectError("down"), {}),
            {
                "outcome": "failed",
                "failure": "provider_unavailable",
                "retryAfter": None,
            },
        ),
    ],
)
def test_read_maps_provider_responses_to_typed_outcomes(
    tmp_path, usage, expected
) -> None:
    provider = Provider(usage=usage)
    assert probe.read(_home(tmp_path), transport=provider.transport()) == expected
    # No redirect is followed and no identity read follows a failed usage read.
    assert len(provider.requests) == 1


def test_failed_profile_read_still_reports_usage(tmp_path) -> None:
    provider = Provider(profile=(500, {}, {}))
    result = probe.read(_home(tmp_path), transport=provider.transport())
    assert result["outcome"] == "observed"
    assert result["profile"] is None
    assert result["usage"]["windows"]["seven_day"]["utilization"] == 42.0


@pytest.mark.parametrize(
    ("oauth", "expected"),
    [
        (
            {"scopes": ["user:inference"]},
            {"outcome": "failed", "failure": "usage_access_missing"},
        ),
        (
            {"expiresAt": int((time.time() - 10) * 1000)},
            {"outcome": "recovery_required"},
        ),
        (
            {"accessToken": "", "refreshToken": "", "expiresAt": 0},
            {"outcome": "failed", "failure": "reconnect_required"},
        ),
    ],
)
def test_read_decides_from_stored_state_without_a_request(
    tmp_path, oauth, expected
) -> None:
    provider = Provider()
    assert probe.read(_home(tmp_path, **oauth), transport=provider.transport()) == (
        expected
    )
    assert provider.requests == []


def test_probe_source_runs_as_a_standalone_program(tmp_path) -> None:
    """The host image runs the shipped text with ``python3 -c``."""

    empty_home = tmp_path / "empty"
    empty_home.mkdir()
    completed = subprocess.run(
        [sys.executable, "-c", claude.PROBE_SOURCE, "read", str(empty_home)],
        capture_output=True,
        timeout=30,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "v": 1,
        "outcome": "failed",
        "failure": "reconnect_required",
    }
    result = claude.parse_probe_output(completed.returncode, completed.stdout)
    assert result.failure is AccountUsageFailureCode.RECONNECT_REQUIRED


# -- probe script: CLI-owned recovery -------------------------------------------

FAKE_CLI = textwrap.dedent(
    """
    import json, os, subprocess, sys, time

    mode = os.environ["FAKE_CLAUDE_MODE"]
    home = os.environ["CLAUDE_CONFIG_DIR"]
    log = open(os.environ["FAKE_CLAUDE_LOG"], "a")
    log.write(json.dumps({"argv": sys.argv[1:], "tmpdir": os.environ.get("TMPDIR")}) + "\\n")
    log.flush()
    if mode == "hang":
        child = subprocess.Popen(["sleep", "300"])
        log.write(json.dumps({"child": child.pid}) + "\\n")
        log.flush()
    for line in sys.stdin:
        log.write(line)
        log.flush()
        message = json.loads(line)
        request = message.get("request") or {}
        if mode == "hang":
            continue
        if request.get("subtype") == "get_usage":
            path = os.path.join(home, ".credentials.json")
            stored = json.load(open(path))["claudeAiOauth"]
            if mode == "renew":
                stored.update(accessToken="sk-ant-oat01-RENEWEDRENEWED", refreshToken="sk-ant-ort01-ROTATEDROTATED", expiresAt=int((time.time() + 28800) * 1000))
            elif mode == "reject":
                stored.update(accessToken="", refreshToken="", expiresAt=0)
            if mode in ("renew", "reject"):
                tmp = path + ".tmp"
                json.dump({"claudeAiOauth": stored}, open(tmp, "w"))
                os.replace(tmp, path)
            if mode == "unsupported":
                response = {"subtype": "error", "request_id": message["request_id"], "error": "Unknown subtype"}
            else:
                response = {"subtype": "success", "request_id": message["request_id"], "response": {"rate_limits_available": True}}
        else:
            response = {"subtype": "success", "request_id": message["request_id"], "response": {}}
        print(json.dumps({"type": "control_response", "response": response}), flush=True)
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    script.chmod(0o755)
    log = tmp_path / "cli.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))

    def run(mode: str, home: str, provider: Provider | None = None):
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
        provider = provider or Provider(token="sk-ant-oat01-RENEWEDRENEWED")
        result = probe.recover(
            home, transport=provider.transport(), command=str(script)
        )
        text = log.read_text() if log.exists() else ""
        return result, provider, [json.loads(line) for line in text.splitlines()]

    return run


def _running(pid: int) -> bool:
    """Alive and not a zombie (an unreaped child cannot write anything)."""

    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _no_prompt(lines: list[dict]) -> None:
    sent = [line for line in lines if "type" in line]
    assert sent and all(line["type"] == "control_request" for line in sent)
    assert [line["request"]["subtype"] for line in sent][:1] == ["initialize"]


def test_idle_expiry_renews_through_the_cli_and_retries_the_read_once(
    tmp_path, fake_cli
) -> None:
    home = _home(tmp_path, expiresAt=int((time.time() - 60) * 1000))
    result, provider, lines = fake_cli("renew", home)

    assert result["outcome"] == "observed"
    assert result["recovery"] == "renewed"
    # Rotated state is the CLI's own persisted write; the probe only read it.
    stored = json.loads(Path(home, ".credentials.json").read_text())["claudeAiOauth"]
    assert stored["refreshToken"] == "sk-ant-ort01-ROTATEDROTATED"
    assert [request.url.path for request in provider.requests] == [
        "/api/oauth/usage",
        "/api/oauth/profile",
    ]
    _no_prompt(lines)
    assert (
        lines[0]["argv"][:1] == ["-p"]
        and "--no-session-persistence" in lines[0]["argv"]
    )
    assert lines[0]["tmpdir"].startswith(str(tmp_path))
    assert "sk-ant" not in json.dumps(result)


def test_a_rejected_refresh_token_requires_reconnect(tmp_path, fake_cli) -> None:
    home = _home(tmp_path, expiresAt=int((time.time() - 60) * 1000))
    result, provider, lines = fake_cli("reject", home)

    assert result == {
        "outcome": "failed",
        "failure": "reconnect_required",
        "recovery": "rejected",
    }
    assert provider.requests == []
    _no_prompt(lines)


def test_a_renewed_token_the_provider_refuses_is_not_renewed_again(
    tmp_path, fake_cli
) -> None:
    home = _home(tmp_path, expiresAt=int((time.time() - 60) * 1000))
    result, provider, lines = fake_cli("renew", home, Provider(token="other"))

    assert result == {
        "outcome": "failed",
        "failure": "credential_refresh_unavailable",
        "recovery": "renewed",
    }
    assert len(provider.requests) == 1
    assert sum(1 for line in lines if "argv" in line) == 1


def test_an_unanswered_cli_is_stopped_with_its_children(
    tmp_path, fake_cli, monkeypatch
) -> None:
    monkeypatch.setattr(probe, "CLI_ANSWER_SECONDS", 1.0)
    home = _home(tmp_path, expiresAt=int((time.time() - 60) * 1000))
    before = Path(home, ".credentials.json").read_text()
    result, provider, lines = fake_cli("hang", home)

    assert result == {
        "outcome": "failed",
        "failure": "credential_refresh_unavailable",
        "recovery": "uncertain",
    }
    assert Path(home, ".credentials.json").read_text() == before
    child = next(line["child"] for line in lines if "child" in line)
    deadline = time.monotonic() + 5
    while _running(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _running(child), "the CLI's process group survived the probe"
    assert provider.requests == []


def test_unchanged_credentials_after_an_answer_are_not_retried(
    tmp_path, fake_cli
) -> None:
    home = _home(tmp_path, expiresAt=int((time.time() - 60) * 1000))
    result, provider, _ = fake_cli("answer_only", home)
    assert result == {
        "outcome": "failed",
        "failure": "credential_refresh_unavailable",
        "recovery": "not_renewed",
    }
    assert provider.requests == []


def test_missing_native_recovery_gives_upgrade_or_reconnect_guidance(
    tmp_path, fake_cli, monkeypatch
) -> None:
    home = _home(tmp_path, expiresAt=int((time.time() - 60) * 1000))
    unsupported, _, _ = fake_cli("unsupported", home)
    assert unsupported["failure"] == "credential_recovery_unsupported"

    monkeypatch.setattr(probe.shutil, "which", lambda _name: None)
    missing = probe.recover(home, transport=Provider().transport())
    assert missing == {
        "outcome": "failed",
        "failure": "credential_recovery_unsupported",
        "recovery": "unsupported",
    }
    assert (
        claude.parse_probe_output(0, _probe_stdout(**missing)).failure
        is AccountUsageFailureCode.CREDENTIAL_RECOVERY_UNSUPPORTED
    )


def test_recovery_never_starts_the_cli_for_credentials_it_cannot_help(
    tmp_path, fake_cli
) -> None:
    home = _home(tmp_path, scopes=["user:inference"], expiresAt=0)
    result, _, lines = fake_cli("renew", home)
    assert result == {"outcome": "failed", "failure": "usage_access_missing"}
    assert lines == []
