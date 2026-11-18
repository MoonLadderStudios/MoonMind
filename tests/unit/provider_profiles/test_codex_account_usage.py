"""Codex OAuth account-usage adapter (MoonLadderStudios/MoonMind#5364).

Recorded, sanitized app-server fixtures and a fake ``codex`` program only: no
credentials, no provider calls, no inference.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from moonmind.provider_profiles import codex_account_usage as codex
from moonmind.provider_profiles import codex_account_usage_probe as probe
from moonmind.provider_profiles.account_usage import (
    AccountUsageFailureCode,
    UsageWindowPeriod,
    UsageWindowScope,
    WindowFreshness,
    remaining_percent,
    window_freshness,
)
from moonmind.provider_profiles.usage_collection import (
    IdleProbeContext,
    UsageCollectionTarget,
    default_usage_collectors,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "provider" / "codex_oauth"
T0 = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
WEEK_RESET = 1795500000
SHORT_RESET = 1795020000


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _account(version: str = "0.170.0") -> dict:
    return _fixture("account_read.json")[version]


def _limits(name: str = "multi_bucket") -> dict:
    return _fixture("rate_limits.json")[name]


def _window(used=40, mins=10080, reset=WEEK_RESET) -> dict:
    return {"usedPercent": used, "windowDurationMins": mins, "resetsAt": reset}


def _bucket(limit_id="codex", primary=None, secondary=None, **extra) -> dict:
    return {
        "limitId": limit_id,
        "limitName": extra.pop("limitName", None),
        "normalModelSlug": extra.pop("normalModelSlug", None),
        "primary": primary,
        "secondary": secondary,
        **extra,
    }


def _projected(account=None, limits=None, account_after=None, **kwargs) -> dict:
    account = _account() if account is None else account
    return probe.project(
        account,
        _limits() if limits is None else limits,
        account if account_after is None else account_after,
        observed_at=kwargs.pop("observed_at", T0.timestamp()),
        codex_version=kwargs.pop("codex_version", "0.170.0"),
    )


def _report(**kwargs):
    return codex.report_from_projection(_projected(**kwargs))


def _windows(report) -> dict:
    return {window.key: window for window in report.usage.windows}


# -- Window classification ------------------------------------------------------


def test_recorded_multi_bucket_read_keeps_pools_and_scopes_separate() -> None:
    report = _report()
    windows = _windows(report)

    assert report.usage.completeness.value == "full"
    assert report.usage.primary_window_key == "codex.weekly"
    weekly = windows["codex.weekly"]
    assert (weekly.period, weekly.scope) == (
        UsageWindowPeriod.WEEKLY,
        UsageWindowScope.GENERAL,
    )
    assert weekly.used_percent == 40
    assert weekly.reset_at == datetime.fromtimestamp(WEEK_RESET, UTC)
    assert weekly.duration_seconds == 7 * 86400
    assert weekly.source_version == "0.170.0"
    assert windows["codex.5h"].period is UsageWindowPeriod.SHORT
    # Another metered pool is never the general allowance, never summed, and
    # never presented as model-scoped unless the protocol says so.
    spark = windows["codex_bengalfox.weekly"]
    assert (spark.period, spark.scope) == (
        UsageWindowPeriod.OTHER,
        UsageWindowScope.GENERAL,
    )
    assert spark.used_percent == 7
    assert windows["codex_bengalfox.5h"].period is UsageWindowPeriod.OTHER
    assert set(windows) == {
        "codex.weekly",
        "codex.5h",
        "codex_bengalfox.weekly",
        "codex_bengalfox.5h",
    }


@pytest.mark.parametrize("slot", ["primary", "secondary"])
def test_a_weekly_window_classifies_in_either_slot(slot) -> None:
    other = "secondary" if slot == "primary" else "primary"
    bucket = _bucket(**{slot: _window(used=25), other: _window(used=5, mins=300)})
    report = _report(limits={"rateLimits": bucket, "rateLimitsByLimitId": None})

    assert report.usage.primary_window_key == "codex.weekly"
    assert _windows(report)["codex.weekly"].used_percent == 25


@pytest.mark.parametrize(
    ("mins", "key", "period"),
    [
        (300, "codex.5h", UsageWindowPeriod.SHORT),
        (1440, "codex.1d", UsageWindowPeriod.SHORT),
        (None, "codex.secondary", UsageWindowPeriod.OTHER),
        (43200, "codex.30d", UsageWindowPeriod.OTHER),
        (10079, "codex.10079m", UsageWindowPeriod.SHORT),
    ],
)
def test_only_a_seven_day_duration_is_weekly(mins, key, period) -> None:
    bucket = _bucket(secondary=_window(mins=mins))
    report = _report(limits={"rateLimits": bucket, "rateLimitsByLimitId": None})

    assert report.usage.primary_window_key is None
    assert _windows(report)[key].period is period


def test_an_explicit_model_scope_is_kept_and_never_replaces_the_general_pool() -> None:
    model_bucket = _bucket(
        "codex_model",
        secondary=_window(used=90),
        limitName="Example Model",
        normalModelSlug="example-model",
    )
    report = _report(
        limits={
            "rateLimits": _bucket(primary=_window(mins=300)),
            "rateLimitsByLimitId": {
                "codex": _bucket(primary=_window(mins=300)),
                "codex_model": model_bucket,
            },
        }
    )
    windows = _windows(report)
    model = windows["codex_model.weekly"]

    assert (model.period, model.scope, model.model_label) == (
        UsageWindowPeriod.WEEKLY,
        UsageWindowScope.MODEL,
        "Example Model",
    )
    # The general pool reported no weekly window: unreported, not substituted.
    assert report.usage.primary_window_key is None
    assert "codex.weekly" not in windows


def test_legacy_single_bucket_is_the_general_pool() -> None:
    report = _report(limits=_limits("legacy"))

    assert report.usage.primary_window_key == "codex.weekly"
    assert _windows(report)["codex.weekly"].used_percent == 55.5
    assert report.account_id is None


def test_multi_bucket_view_wins_over_its_legacy_mirror() -> None:
    limits = {
        "rateLimits": _bucket(secondary=_window(used=1)),
        "rateLimitsByLimitId": {"codex": _bucket(secondary=_window(used=2))},
    }

    assert _windows(_report(limits=limits))["codex.weekly"].used_percent == 2


def test_empty_response_is_a_full_snapshot_with_nothing_reported() -> None:
    report = _report(limits={"rateLimits": _bucket(), "rateLimitsByLimitId": {}})

    assert report.usage.windows == ()
    assert report.usage.primary_window_key is None


def test_percentage_and_reset_stay_independently_nullable() -> None:
    bucket = _bucket(
        primary={"usedPercent": None, "windowDurationMins": 300, "resetsAt": 5},
        secondary={"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": None},
    )
    windows = _windows(_report(limits={"rateLimits": bucket}))

    assert windows["codex.5h"].used_percent is None
    assert windows["codex.5h"].reset_at == datetime.fromtimestamp(5, UTC)
    assert windows["codex.weekly"].reset_at is None


def test_unix_seconds_resets_and_a_passed_reset_never_refill() -> None:
    weekly = _windows(_report())["codex.weekly"]
    reset = datetime.fromtimestamp(WEEK_RESET, UTC)

    assert reset.isoformat() == "2026-11-24T06:00:00+00:00"
    after = reset + timedelta(seconds=1)
    assert (
        window_freshness(observed_at=T0, reset_at=weekly.reset_at, now=after)
        is WindowFreshness.EXPIRED
    )
    # The last known reading stays 60% remaining; it is never shown as 100%.
    assert remaining_percent(weekly.used_percent) == 60


@pytest.mark.parametrize(
    "window",
    [
        {"usedPercent": -1, "windowDurationMins": 10080, "resetsAt": 1},
        {"usedPercent": "lots", "windowDurationMins": 10080, "resetsAt": 1},
        {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 1795500000000},
        {"usedPercent": 1, "windowDurationMins": 0, "resetsAt": 1},
        {"usedPercent": 1, "windowDurationMins": "week", "resetsAt": 1},
        "not a window",
    ],
)
def test_unreadable_windows_fail_the_reading_instead_of_vanishing(window) -> None:
    projected = _projected(limits={"rateLimits": _bucket(secondary=window)})

    with pytest.raises(codex.MalformedCodexUsage):
        codex.report_from_projection(projected)


def test_overage_is_kept_and_remaining_is_clamped() -> None:
    bucket = _bucket(secondary=_window(used=130))
    weekly = _windows(_report(limits={"rateLimits": bucket}))["codex.weekly"]

    assert weekly.used_percent == 130
    assert remaining_percent(weekly.used_percent) == 0


# -- Identity --------------------------------------------------------------------


def test_identity_binds_the_chatgpt_account_and_keeps_display_fields() -> None:
    report = _report()

    assert report.account_id == "acct-1111"
    assert report.identity.account_id == "acct-1111"
    assert report.identity.verified is True
    assert report.identity.email == "person@example.com"
    assert report.identity.plan == "plus"
    assert report.identity.display_name == "Example Workspace"


def test_older_protocol_without_user_or_workspace_fields_stays_usable() -> None:
    limits = dict(_limits(), accountId=None)
    report = _report(account=_account("0.165.0"), limits=limits)

    assert report.identity.display_name is None
    assert report.identity.email == "person@example.com"
    # Routing discovery still names the account, so the reading is bound.
    assert report.identity.account_id == "acct-1111"
    assert report.usage.primary_window_key == "codex.weekly"


def test_without_any_account_id_the_reading_stays_profile_scoped() -> None:
    account = dict(_account("0.165.0"), workspaceRouting=None)
    report = _report(account=account, limits=dict(_limits(), accountId=None))

    assert report.account_id is None
    assert report.identity.account_id is None
    assert report.identity.verified is False
    assert report.identity.email == "person@example.com"


def test_missing_email_keeps_valid_usage() -> None:
    account = {**_account(), "account": {**_account()["account"], "email": None}}
    report = _report(account=account)

    assert report.identity.email is None
    assert report.usage.primary_window_key == "codex.weekly"


def test_a_failed_account_read_keeps_valid_usage_without_identity() -> None:
    projected = probe.project(
        None, _limits(), _account(), observed_at=T0.timestamp(), codex_version=None
    )
    report = codex.report_from_projection(projected)

    assert report.identity is None
    assert report.account_id == "acct-1111"
    assert report.usage.primary_window_key == "codex.weekly"


def test_mismatched_routing_and_usage_accounts_are_both_reported() -> None:
    # CORE rejects an observation carrying two account IDs as a mismatch.
    report = _report(limits=dict(_limits(), accountId="acct-2222"))

    assert {report.account_id, report.identity.account_id} == {
        "acct-1111",
        "acct-2222",
    }


@pytest.mark.parametrize(
    "after",
    [
        {**_account(), "userId": "user-2"},
        {**_account(), "account": {**_account()["account"], "email": "b@example.com"}},
        {
            **_account(),
            "workspaceRouting": {
                **_account()["workspaceRouting"],
                "chatgptAccountId": "acct-2222",
            },
        },
    ],
)
def test_an_account_switch_during_the_read_pair_is_not_reported(after) -> None:
    assert _projected(account_after=after) is None


def test_projection_carries_allowlisted_fields_only() -> None:
    limits = _limits()
    limits["rateLimitUpsell"] = {"banner": "upgrade"}
    limits["rateLimits"]["credits"] = {"balance": "12"}
    projected = _projected(limits=limits)
    text = json.dumps(projected)

    for absent in ("credits", "balance", "upsell", "Upsell", "backendOrigin", "user-1"):
        assert absent not in text
    assert set(projected) == {
        "observed_at",
        "codex_version",
        "account",
        "routing_account_id",
        "account_id",
        "rate_limits",
        "rate_limits_by_limit_id",
    }


def test_secret_shaped_display_values_are_dropped() -> None:
    account = {
        **_account(),
        "account": {**_account()["account"], "workspaceName": "Bearer abcdefghijk"},
    }

    assert _report(account=account).identity.display_name is None


# -- Probe output ------------------------------------------------------------------


def _probe_line(payload: dict) -> bytes:
    return (json.dumps({"v": 1, **payload}) + "\n").encode()


def test_observed_probe_output_reports_usage_and_identity() -> None:
    parsed = codex.parse_probe_output(
        0, _probe_line({"outcome": "observed", "usage": _projected()})
    )

    assert parsed.report.usage.primary_window_key == "codex.weekly"
    assert parsed.report.identity.email == "person@example.com"


@pytest.mark.parametrize(
    ("exit_code", "stdout", "failure"),
    [
        (137, b"", AccountUsageFailureCode.TIMEOUT),
        (1, b"noise", AccountUsageFailureCode.INTERNAL_ERROR),
        (0, b'{"v":1,"outcome":"failed","failure":"rate_limited"}', "rate_limited"),
        (0, b'{"v":1,"outcome":"failed","failure":"made_up"}', "internal_error"),
        (
            0,
            b'{"v":1,"outcome":"observed","usage":{"observed_at":"x"}}',
            "malformed_response",
        ),
    ],
)
def test_probe_failures_are_typed(exit_code, stdout, failure) -> None:
    parsed = codex.parse_probe_output(exit_code, stdout)

    assert parsed.failure == AccountUsageFailureCode(failure)


def test_probe_output_can_request_native_recovery() -> None:
    parsed = codex.parse_probe_output(0, _probe_line({"outcome": "recovery_required"}))

    assert parsed.recovery_required and parsed.report is None


# -- Adapter -------------------------------------------------------------------------


def _context() -> IdleProbeContext:
    return IdleProbeContext(
        target=UsageCollectionTarget(
            profile_id="codex-oauth",
            credential_generation=3,
            provider_id="openai",
            runtime_id="codex_cli",
            attempt_id="attempt-1",
            deadline_at=T0 + timedelta(minutes=2),
        ),
        credential_home_path="/home/app/.codex",
    )


@pytest.mark.asyncio
async def test_adapter_is_registered_for_openai_oauth_on_codex_cli() -> None:
    registry = default_usage_collectors()

    adapter = registry.get("codex_cli", "openai")
    assert isinstance(adapter, codex.CodexUsageCollector)
    assert registry.get("codex_cli", "openrouter") is None
    assert registry.get("codex_cli", "minimax") is None
    # A running owner publishes from its own app-server connection.
    assert await adapter.request_from_owner(object()) is None


def test_every_codex_probe_persists_native_renewal_in_the_enrolled_home() -> None:
    adapter = codex.CodexUsageCollector()
    read = adapter.idle_probe(_context())
    recover = adapter.recovery_probe(_context())

    assert read.argv[:2] == ("python3", "-c")
    assert read.argv[3:] == ("read", "/home/app/.codex")
    assert recover.argv[3:] == ("recover", "/home/app/.codex")
    # Codex renews an expired token when its app-server starts, so a
    # read-only home would discard the rotated refresh token.
    assert read.writes_credentials and recover.writes_credentials
    assert read.timeout_seconds < recover.timeout_seconds <= 120


# -- Native owner events ---------------------------------------------------------------


def test_native_report_is_a_full_snapshot_from_the_owner_projection() -> None:
    report = codex.native_report(_projected())

    assert report.usage.completeness.value == "full"
    assert report.usage.observed_at == T0
    assert report.account_id == "acct-1111"


def test_native_report_ignores_unreadable_events() -> None:
    assert codex.native_report({"observed_at": "soon"}) is None
    assert codex.native_report("not a mapping") is None


# -- Probe program against a fake app-server -------------------------------------------------

_FAKE_CODEX = textwrap.dedent(
    """
    import json, os, sys
    scenario = json.loads(os.environ["FAKE_CODEX_SCENARIO"])
    log = open(os.environ["FAKE_CODEX_LOG"], "a")
    reads = 0
    for line in sys.stdin:
        message = json.loads(line)
        log.write(json.dumps(message) + "\\n"); log.flush()
        if "id" not in message:
            continue
        method = message["method"]
        if method == "initialize":
            reply = {"result": {"userAgent": "moonmind-usage/0.170.0 (Linux)"}}
        elif method == "account/read":
            reads += 1
            key = "account_forced" if message["params"].get("refreshToken") else (
                "account" if reads == 1 or "account_after" not in scenario
                else "account_after")
            reply = scenario.get(key) or scenario["account"]
        else:
            reply = scenario["limits"]
        print(json.dumps({"method": "account/updated", "params": {}}))
        print(json.dumps({"id": message["id"], **reply}), flush=True)
    """
)


@pytest.fixture
def fake_codex(tmp_path):
    program = tmp_path / "codex"
    program.write_text(f"#!{sys.executable}\n{_FAKE_CODEX}")
    program.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "log.jsonl"

    def run(mode: str, scenario: dict, *, login: bool = True) -> tuple[dict, list]:
        if login:
            (home / "auth.json").write_text("{}")
        env = {
            **os.environ,
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "FAKE_CODEX_SCENARIO": json.dumps(scenario),
            "FAKE_CODEX_LOG": str(log),
            "OPENAI_API_KEY": "sk-should-not-leak-1234567890",
        }
        completed = subprocess.run(
            [sys.executable, "-c", codex.PROBE_SOURCE, mode, str(home)],
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        sent = [json.loads(line) for line in log.read_text().splitlines()]
        log.write_text("")
        return json.loads(completed.stdout.decode().strip().splitlines()[-1]), sent

    return run


def _ok(result) -> dict:
    return {"result": result}


def _error(message: str, code: int = -32603) -> dict:
    return {"error": {"code": code, "message": message}}


def test_probe_reads_the_pair_without_forcing_renewal(fake_codex) -> None:
    output, sent = fake_codex(
        "read", {"account": _ok(_account()), "limits": _ok(_limits())}
    )

    assert output["outcome"] == "observed"
    assert output["usage"]["codex_version"] == "0.170.0"
    methods = [(m.get("method"), m.get("params")) for m in sent]
    assert methods == [
        ("initialize", methods[0][1]),
        ("initialized", None),
        ("account/read", {"refreshToken": False}),
        ("account/rateLimits/read", {"excludeResetCreditDetails": True}),
        ("account/read", {"refreshToken": False}),
    ]
    assert "turn/start" not in json.dumps(sent)
    report = codex.parse_probe_output(0, json.dumps({"v": 1, **output}).encode())
    assert report.report.identity.email == "person@example.com"


def test_probe_retries_without_an_optional_parameter_the_protocol_rejects(
    fake_codex,
) -> None:
    scenario = {
        "account": _ok(_account()),
        "limits": _error("unknown field `excludeResetCreditDetails`", -32602),
    }
    output, sent = fake_codex("read", scenario)

    assert [m.get("params") for m in sent if "rateLimits" in m.get("method", "")] == [
        {"excludeResetCreditDetails": True},
        {},
    ]
    assert output == {"v": 1, "outcome": "failed", "failure": "provider_unavailable"}


@pytest.mark.parametrize(
    ("mode", "scenario", "expected"),
    [
        (
            "read",
            {"account": _ok({"account": {"type": "apiKey"}})},
            {"outcome": "failed", "failure": "usage_access_missing"},
        ),
        (
            "read",
            {
                "account": _ok(_account()),
                "limits": _error(
                    "failed to fetch codex rate limits: GET https://x/wham/usage "
                    'failed: 401 Unauthorized; body={"secret":"sk-abcdefghijkl"}'
                ),
            },
            {"outcome": "recovery_required"},
        ),
        (
            "recover",
            {
                "account": _ok(_account()),
                "limits": _error("GET /wham/usage failed: 401 Unauthorized"),
            },
            {"outcome": "failed", "failure": "credential_refresh_unavailable"},
        ),
        (
            "read",
            {
                "account": _ok(_account()),
                "limits": _error("GET /wham/usage failed: 429 Too Many Requests"),
            },
            {"outcome": "failed", "failure": "rate_limited"},
        ),
        (
            "read",
            {
                "account": _ok(_account()),
                "limits": _error("GET /wham/usage failed: 403 Forbidden"),
            },
            {"outcome": "failed", "failure": "usage_access_missing"},
        ),
        (
            "read",
            {"account": _ok(_account()), "limits": _ok({"rateLimits": "garbage"})},
            {"outcome": "failed", "failure": "malformed_response"},
        ),
        (
            "read",
            {
                "account": _ok(_account()),
                "account_after": _ok({**_account(), "userId": "user-9"}),
                "limits": _ok(_limits()),
            },
            {"outcome": "failed", "failure": "provider_unavailable"},
        ),
        (
            "read",
            {"account": _ok(_fixture("account_read.json")["signed_out"])},
            {"outcome": "recovery_required"},
        ),
        (
            "recover",
            {
                "account": _ok(_fixture("account_read.json")["signed_out"]),
                "account_forced": _ok(_fixture("account_read.json")["signed_out"]),
            },
            {"outcome": "failed", "failure": "credential_refresh_unavailable"},
        ),
    ],
)
def test_probe_outcomes_are_typed_and_never_echo_provider_text(
    fake_codex, mode, scenario, expected
) -> None:
    output, _sent = fake_codex(mode, scenario)

    assert output == {"v": 1, **expected}


def test_probe_without_a_stored_login_requires_reconnect(fake_codex) -> None:
    scenario = {"account": _ok(_fixture("account_read.json")["signed_out"])}

    output, _sent = fake_codex("read", scenario, login=False)

    assert output == {"v": 1, "outcome": "failed", "failure": "reconnect_required"}


def test_recovery_forces_one_native_renewal_then_reads(fake_codex) -> None:
    scenario = {"account": _ok(_account()), "limits": _ok(_limits())}

    output, sent = fake_codex("recover", scenario)

    reads = [m["params"] for m in sent if m.get("method") == "account/read"]
    assert reads == [
        {"refreshToken": True},
        {"refreshToken": False},
        {"refreshToken": False},
    ]
    assert output["outcome"] == "observed"


def test_probe_hides_inherited_api_keys_from_codex(tmp_path) -> None:
    seen = tmp_path / "seen.json"
    program = tmp_path / "codex"
    program.write_text(
        f"#!{sys.executable}\nimport json, os\n"
        f"open({str(seen)!r}, 'w').write(json.dumps("
        "{key: key in os.environ for key in "
        "('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'CODEX_HOME')}))\n"
    )
    program.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "OPENAI_API_KEY": "sk-should-not-leak-1234567890",
        "CODEX_API_KEY": "sk-should-not-leak-1234567890",
        "CODEX_ACCESS_TOKEN": "should-not-leak",
    }

    completed = subprocess.run(
        [sys.executable, "-c", codex.PROBE_SOURCE, "read", str(tmp_path)],
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert json.loads(seen.read_text()) == {
        "OPENAI_API_KEY": False,
        "CODEX_API_KEY": False,
        "CODEX_ACCESS_TOKEN": False,
        "CODEX_HOME": True,
    }
    assert b"should-not-leak" not in completed.stdout
    assert json.loads(completed.stdout)["outcome"] == "failed"
