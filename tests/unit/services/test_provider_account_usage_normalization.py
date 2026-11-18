"""Normalization fixtures for the provider account-usage contract (#5362)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from random import Random

import pytest
from pydantic import ValidationError

from moonmind.provider_profiles.account_usage import (
    MAX_BACKOFF,
    MIN_BACKOFF,
    STALE_AFTER,
    AccountIdentityInput,
    AccountUsageNormalizationError,
    AccountUsageObservation,
    UsageInput,
    UsageWindowInput,
    WindowFreshness,
    backoff_delay,
    normalize_instant,
    normalize_used_percent,
    parse_retry_after,
    remaining_percent,
    window_fresh_until,
    window_freshness,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _window(**overrides):
    values = {
        "key": "weekly",
        "period": "weekly",
        "source": "fake_collector",
        "used_percent": 10,
    }
    values.update(overrides)
    return UsageWindowInput(**values)


@pytest.mark.parametrize(
    ("used", "remaining"),
    [
        (0, 100.0),
        (1, 99.0),  # 1 means 1%, never 100%.
        (100, 0.0),
        (37.5, 62.5),
        ("42", 58.0),
        ("0.5", 99.5),
        (Decimal("12.25"), 87.75),
        (125, 0.0),  # Valid overage clamps the displayed value.
    ],
)
def test_used_percent_derives_clamped_remaining(used, remaining) -> None:
    window = _window(used_percent=used)
    assert remaining_percent(window.used_percent) == remaining


def test_overage_keeps_typed_exhaustion_information() -> None:
    window = _window(used_percent=125)
    assert window.used_percent == 125.0
    assert remaining_percent(window.used_percent) == 0.0


def test_null_percentage_and_reset_stay_independent() -> None:
    no_percent = _window(used_percent=None, reset_at="2026-11-20T00:00:00Z")
    assert no_percent.used_percent is None
    assert remaining_percent(no_percent.used_percent) is None
    assert no_percent.reset_at == datetime(2026, 11, 20, tzinfo=UTC)

    no_reset = _window(used_percent=0)
    assert no_reset.used_percent == 0.0
    assert no_reset.reset_at is None


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        math.nan,
        math.inf,
        -math.inf,
        "NaN",
        "inf",
        "1e400",
        "12%",
        "",
        "  ",
        "abc",
        "1,5",
        -1,
        [],
        {"value": 1},
    ],
)
def test_malformed_used_percentages_are_rejected(value) -> None:
    with pytest.raises(AccountUsageNormalizationError):
        normalize_used_percent(value)
    with pytest.raises(ValidationError):
        _window(used_percent=value)


def test_unknown_units_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _window(used_unit="ratio")
    with pytest.raises(ValidationError):
        _window(used_unit="requests")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-20T00:00:00Z", datetime(2026, 11, 20, tzinfo=UTC)),
        ("2026-11-20T02:00:00+02:00", datetime(2026, 11, 20, tzinfo=UTC)),
        (
            datetime(2026, 11, 19, 19, 0, tzinfo=timezone(timedelta(hours=-5))),
            datetime(2026, 11, 20, tzinfo=UTC),
        ),
        (1795132800, datetime(2026, 11, 20, tzinfo=UTC)),
    ],
)
def test_reset_timestamps_normalize_to_absolute_utc(value, expected) -> None:
    instant = normalize_instant(value)
    assert instant == expected
    assert instant.tzinfo == UTC


@pytest.mark.parametrize(
    "value", ["2026-11-20T00:00:00", datetime(2026, 11, 20), True, "soon", -5]
)
def test_ambiguous_or_malformed_resets_are_rejected(value) -> None:
    with pytest.raises(AccountUsageNormalizationError):
        normalize_instant(value)


def test_freshness_expires_at_earlier_of_ttl_or_reset() -> None:
    observed = NOW
    assert window_fresh_until(observed, None) == observed + STALE_AFTER
    soon = observed + timedelta(minutes=2)
    assert window_fresh_until(observed, soon) == soon
    later = observed + timedelta(days=3)
    assert window_fresh_until(observed, later) == observed + STALE_AFTER

    assert (
        window_freshness(observed_at=observed, reset_at=later, now=NOW)
        is WindowFreshness.FRESH
    )
    assert (
        window_freshness(observed_at=observed, reset_at=later, now=NOW + STALE_AFTER)
        is WindowFreshness.STALE
    )
    assert (
        window_freshness(observed_at=observed, reset_at=soon, now=soon)
        is WindowFreshness.EXPIRED
    )


def test_scoped_windows_never_stand_in_for_the_general_allowance() -> None:
    model_weekly = _window(key="weekly-opus", scope="model", model_label="Opus")
    short = _window(key="five-hour", period="short", duration_seconds=18000)
    with pytest.raises(ValidationError, match="general weekly"):
        UsageInput(
            completeness="full",
            observed_at=NOW,
            windows=[model_weekly, short],
            primary_window_key="weekly-opus",
        )
    with pytest.raises(ValidationError, match="general weekly"):
        UsageInput(
            completeness="full",
            observed_at=NOW,
            windows=[model_weekly, short],
            primary_window_key="five-hour",
        )
    with pytest.raises(ValidationError, match="model_label"):
        _window(scope="model")
    with pytest.raises(ValidationError, match="model_label"):
        _window(model_label="Opus")
    with pytest.raises(ValidationError, match="unique"):
        UsageInput(completeness="full", observed_at=NOW, windows=[_window(), _window()])


def test_contract_allowlists_fields_and_rejects_credential_material() -> None:
    with pytest.raises(ValidationError):
        _window(raw_body={"rate_limits": {}})
    with pytest.raises(ValidationError):
        AccountIdentityInput(
            source="fake", observed_at=NOW, access_token="eyJhbGciOi.eyJzdWIi.sig"
        )
    for value in (
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2ln",
        "Bearer abcdefghijklmnop",
        "sk-abcdefghijklmnop1234",
        "token=abcdefgh12345678",
    ):
        with pytest.raises(ValidationError):
            AccountIdentityInput(source="fake", observed_at=NOW, display_name=value)
        with pytest.raises(ValidationError):
            AccountIdentityInput(source="fake", observed_at=NOW, plan=value)
    with pytest.raises(ValidationError):
        AccountIdentityInput(source="fake", observed_at=NOW, email="not-an-email")
    with pytest.raises(ValidationError, match="account_id"):
        AccountIdentityInput(source="fake", observed_at=NOW, verified=True)
    with pytest.raises(ValidationError):
        AccountUsageObservation(
            profile_id="p",
            credential_generation=1,
            provider_id="openai",
            runtime_id="codex_cli",
            authority={"kind": "native_owner", "owner_expires_at": NOW},
            headers={"Authorization": "Bearer x"},
        )

    identity = AccountIdentityInput(
        source="fake",
        observed_at=NOW,
        email="person@example.com",
        display_name="Person Example",
        plan="pro",
    )
    assert identity.email == "person@example.com"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("120", timedelta(seconds=120)),
        (30, timedelta(seconds=30)),
        ("Wed, 18 Nov 2026 12:05:00 GMT", timedelta(minutes=5)),
        ("Wed, 18 Nov 2026 11:00:00 GMT", timedelta(0)),
        ("soon", None),
        ("-5", None),
        (True, None),
        (math.nan, None),
        (None, None),
    ],
)
def test_retry_after_accepts_seconds_and_http_dates(value, expected) -> None:
    assert parse_retry_after(value, now=NOW) == expected


def test_backoff_has_floor_honors_longer_provider_delay_and_jitters() -> None:
    rng = Random(7)
    first = backoff_delay(consecutive_failures=1, retry_after=None, rng=rng)
    assert MIN_BACKOFF <= first <= MIN_BACKOFF * 1.2
    tiny = backoff_delay(
        consecutive_failures=1, retry_after=timedelta(seconds=1), rng=Random(1)
    )
    assert tiny >= MIN_BACKOFF
    provider = backoff_delay(
        consecutive_failures=1, retry_after=timedelta(hours=3), rng=Random(1)
    )
    assert provider >= timedelta(hours=3)
    capped = backoff_delay(consecutive_failures=40, retry_after=None, rng=Random(1))
    assert MAX_BACKOFF <= capped <= MAX_BACKOFF * 1.2
    jittered = {
        backoff_delay(consecutive_failures=2, retry_after=None, rng=Random(seed))
        for seed in range(5)
    }
    assert len(jittered) > 1
