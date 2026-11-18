"""Claude Code OAuth account-usage collection (MoonLadderStudios/MoonMind#5365).

Two sources feed the shared account-usage contract for Anthropic OAuth
profiles on the ``claude_code`` runtime:

* **Native status line.** While a claude-native interactive session runs,
  the Omnigent status-line capture forwards ``rate_limits.five_hour`` and
  ``rate_limits.seven_day``. Percentages there are 0-100 and resets are epoch
  seconds. Each window carries the time its values last changed, so a
  re-rendered cached reading keeps its original time.
  :func:`status_line_report` turns that into a partial observation.
  Headless and direct Claude launches have no status line and report nothing
  here.
* **Idle reads.** :class:`ClaudeUsageCollector` runs
  :mod:`claude_account_usage_probe` in the installed host image under the
  exclusive usage-observation grant. The probe reads the internal
  ``/api/oauth/usage`` and ``/api/oauth/profile`` routes with the profile's
  own stored token. ``utilization`` is a percentage (1.0 means 1%) and resets
  are ISO 8601. When that token has expired, the recovery probe lets the
  installed Claude Code CLI renew it, then reads once more.

These routes are undocumented, so parsing tolerates unknown fields and keys.
A recognized field with an unreadable value fails the reading as
``malformed_response``. The last good values are kept, with their times;
no value is guessed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from moonmind.provider_profiles.account_usage import (
    AccountIdentityInput,
    AccountUsageFailureCode,
    AccountUsageNormalizationError,
    UsageCompleteness,
    UsageInput,
    UsageWindowInput,
    UsageWindowPeriod,
    UsageWindowScope,
    normalize_instant,
    normalize_used_percent,
)
from moonmind.provider_profiles.usage_collection import (
    IdleProbeContext,
    OwnerUsageReport,
    OwnerUsageRequest,
    ProbeResult,
    UsageProbePlan,
)

RUNTIME_ID = "claude_code"
PROVIDER_ID = "anthropic"
SOURCE_HTTP = "claude_oauth_usage"
SOURCE_PROFILE = "claude_oauth_profile"
SOURCE_STATUS_LINE = "claude_status_line"

_HOUR = 3600
_WEEK = 7 * 86_400
#: General windows, reported under these keys by both sources.
_GENERAL = {
    "seven_day": (UsageWindowPeriod.WEEKLY, _WEEK),
    "five_hour": (UsageWindowPeriod.SHORT, 5 * _HOUR),
}
#: Legacy model-scoped weekly pools.
_LEGACY_MODEL = {"seven_day_opus": "Opus", "seven_day_sonnet": "Sonnet"}
#: The weekly pool for third-party OAuth apps: neither a model nor the
#: general allowance, so it is reported as another limit.
_LEGACY_OTHER = {"seven_day_oauth_apps": _WEEK}
#: ``limits[]`` kinds this adapter knows, by the general key of their pool.
_LIMIT_KINDS = {
    "session": "five_hour",
    "weekly": "seven_day",
    "weekly_scoped": "seven_day",
}
_GENERAL_GROUPS = {None, "subscription"}
_MODEL_GROUPS = {None, "subscription", "weekly"}
_SLUG = re.compile(r"[^a-z0-9]+")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")

#: Probe source shipped to ``python3 -c``; MoonMind is not in the host image.
PROBE_SOURCE = (
    Path(__file__)
    .with_name("claude_account_usage_probe.py")
    .read_text(encoding="utf-8")
)
READ_TIMEOUT_SECONDS = 45
#: Covers the CLI's renewal and the read that follows it.
RECOVERY_TIMEOUT_SECONDS = 100


class _Malformed(Exception):
    """A recognized field held a value that is not a percentage or instant."""


def _percent(value: Any) -> float | None:
    try:
        return normalize_used_percent(value)
    except AccountUsageNormalizationError as exc:
        raise _Malformed from exc


def _instant(value: Any) -> datetime | None:
    try:
        return normalize_instant(value)
    except AccountUsageNormalizationError as exc:
        raise _Malformed from exc


def _window(
    key: str,
    *,
    period: UsageWindowPeriod,
    duration: int,
    used: Any,
    reset: Any,
    source: str,
    model_label: str | None = None,
    observed_at: datetime | None = None,
    source_version: str | None = None,
) -> UsageWindowInput | None:
    used_percent, reset_at = _percent(used), _instant(reset)
    if used_percent is None and reset_at is None:
        # An absent numeric window is "not reported", never a guessed value.
        return None
    return UsageWindowInput(
        key=key,
        period=period,
        scope=UsageWindowScope.MODEL if model_label else UsageWindowScope.GENERAL,
        model_label=model_label,
        duration_seconds=duration,
        used_percent=used_percent,
        reset_at=reset_at,
        source=source,
        source_version=source_version,
        observed_at=observed_at,
    )


def _model_key(general_key: str, label: str) -> str | None:
    slug = _SLUG.sub("_", label.lower()).strip("_")[:32]
    return f"{general_key}_{slug}" if slug else None


def usage_from_oauth_usage(
    projected: Mapping[str, Any], observed_at: datetime
) -> UsageInput:
    """Map a projected ``/api/oauth/usage`` body to a full snapshot.

    Legacy keys are read first. ``limits[]`` fills only pools they did not
    report. A model entry's key is derived from its general pool and model
    name, so ``seven_day_opus`` and a weekly ``Opus`` entry stay one window.
    Unknown keys and kinds are ignored, never classified as weekly. The
    ``is_active`` flag is ignored.

    :raises _Malformed: A recognized field held an unreadable value.
    """

    raw_windows = projected.get("windows")
    raw_limits = projected.get("limits")
    if not isinstance(raw_windows, Mapping) or not (
        raw_limits is None or isinstance(raw_limits, list)
    ):
        raise _Malformed
    recognized = {*_GENERAL, *_LEGACY_MODEL, *_LEGACY_OTHER}
    if not recognized.intersection(raw_windows) and raw_limits is None:
        # Nothing this adapter knows answered: a changed schema, not "none".
        raise _Malformed
    if any(
        raw_windows.get(key) is not None and not isinstance(raw_windows[key], Mapping)
        for key in recognized
    ):
        # Skipping it would read as "not reported" and erase the cached value.
        raise _Malformed
    legacy = [
        (key, period, duration, None) for key, (period, duration) in _GENERAL.items()
    ]
    legacy += [
        (key, UsageWindowPeriod.WEEKLY, _WEEK, label)
        for key, label in _LEGACY_MODEL.items()
    ]
    legacy += [
        (key, UsageWindowPeriod.OTHER, duration, None)
        for key, duration in _LEGACY_OTHER.items()
    ]
    # (key, period, duration, model label, used, reset) in precedence order.
    candidates = [
        (key, period, duration, label, entry.get("utilization"), entry.get("resets_at"))
        for key, period, duration, label in legacy
        if isinstance(entry := raw_windows.get(key), Mapping)
    ]
    for entry in raw_limits or ():
        general_key = (
            _LIMIT_KINDS.get(entry.get("kind")) if isinstance(entry, Mapping) else None
        )
        if general_key is None:
            continue
        model = entry.get("model")
        if model:
            key = _model_key(general_key, model)
            # A model key may only coincide with the legacy key of its pool.
            if (
                entry.get("group") not in _MODEL_GROUPS
                or key is None
                or key in recognized.difference(_LEGACY_MODEL)
            ):
                continue
        elif entry.get("scoped") or entry.get("group") not in _GENERAL_GROUPS:
            # A scope or group this adapter does not know is a different pool.
            continue
        else:
            key = general_key
        period, duration = _GENERAL[general_key]
        candidates.append(
            (key, period, duration, model, entry.get("percent"), entry.get("resets_at"))
        )
    windows: dict[str, UsageWindowInput] = {}
    for key, period, duration, label, used, reset in candidates:
        if key in windows:
            continue
        window = _window(
            key,
            period=period,
            duration=duration,
            used=used,
            reset=reset,
            source=SOURCE_HTTP,
            model_label=label,
        )
        if window is not None:
            windows[key] = window
    return UsageInput(
        completeness=UsageCompleteness.FULL,
        observed_at=observed_at,
        windows=tuple(windows.values())[:32],
        primary_window_key="seven_day" if "seven_day" in windows else None,
    )


def identity_from_oauth_profile(
    projected: Mapping[str, Any] | None, observed_at: datetime
) -> tuple[str | None, str | None, AccountIdentityInput | None]:
    """Return the trusted account and workspace IDs and the display identity.

    Account and organization UUIDs come from the provider's authenticated
    profile for the same token as the usage, so they bind the observation.
    A malformed field is dropped; it never borrows another source.
    """

    if not isinstance(projected, Mapping):
        return None, None, None
    account = projected.get("account") or {}
    organization = projected.get("organization") or {}
    values: dict[str, Any] = {}
    for field, value in (
        ("account_id", account.get("uuid")),
        ("workspace_id", organization.get("uuid")),
        ("email", account.get("email")),
        ("display_name", organization.get("name")),
    ):
        if value is None:
            continue
        try:
            AccountIdentityInput(
                **{field: value}, source=SOURCE_PROFILE, observed_at=observed_at
            )
        except ValidationError:
            continue
        values[field] = value
    if not values:
        return None, None, None
    identity = AccountIdentityInput(
        **values,
        verified=bool(values.get("account_id")),
        source=SOURCE_PROFILE,
        observed_at=observed_at,
    )
    return identity.account_id, identity.workspace_id, identity


def status_line_report(rate_limits: Mapping[str, Any]) -> OwnerUsageReport | None:
    """Partial observation from forwarded status-line quota, or ``None``.

    Each window keeps the time its values last changed, so it replaces only
    an older reading of the same window and leaves the others alone. A
    window that cannot be read is skipped; the rest still count.
    """

    version = rate_limits.get("claude_code_version")
    version = version if isinstance(version, str) and _VERSION.match(version) else None
    windows = []
    for key, (period, duration) in _GENERAL.items():
        entry = rate_limits.get(key)
        if not isinstance(entry, Mapping):
            continue
        try:
            observed = _instant(entry.get("observed_at"))
            if observed is None:
                continue
            window = _window(
                key,
                period=period,
                duration=duration,
                used=entry.get("used_percentage"),
                reset=entry.get("resets_at"),
                source=SOURCE_STATUS_LINE,
                observed_at=observed,
                source_version=version,
            )
        except (_Malformed, ValidationError):
            continue
        if window is not None:
            windows.append(window)
    if not windows:
        return None
    return OwnerUsageReport(
        usage=UsageInput(
            completeness=UsageCompleteness.PARTIAL,
            observed_at=max(window.observed_at for window in windows),
            windows=tuple(windows),
        )
    )


_FAILURES = {code.value: code for code in AccountUsageFailureCode}


def parse_probe_output(exit_code: int, stdout: bytes) -> ProbeResult:
    """Translate the probe's allowlisted JSON line into a report or failure."""

    lines = stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        payload = json.loads(lines[-1]) if lines else None
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        # 137: ``timeout -s KILL`` stopped the probe at its deadline.
        return ProbeResult(
            failure=(
                AccountUsageFailureCode.TIMEOUT
                if exit_code == 137
                else AccountUsageFailureCode.INTERNAL_ERROR
            )
        )
    outcome = payload.get("outcome")
    if outcome == "recovery_required":
        return ProbeResult(recovery_required=True)
    if outcome != "observed":
        failure = _FAILURES.get(str(payload.get("failure")))
        return ProbeResult(
            failure=failure or AccountUsageFailureCode.INTERNAL_ERROR,
            retry_after=(
                payload.get("retryAfter")
                if failure is AccountUsageFailureCode.RATE_LIMITED
                else None
            ),
        )
    try:
        observed_at = _instant(payload.get("observedAt"))
        if observed_at is None:
            raise _Malformed
        usage = usage_from_oauth_usage(payload.get("usage") or {}, observed_at)
    except (_Malformed, ValidationError):
        return ProbeResult(failure=AccountUsageFailureCode.MALFORMED_RESPONSE)
    account_id, workspace_id, identity = identity_from_oauth_profile(
        payload.get("profile"), observed_at
    )
    return ProbeResult(
        report=OwnerUsageReport(
            account_id=account_id,
            workspace_id=workspace_id,
            identity=identity,
            usage=usage,
        )
    )


class ClaudeUsageCollector:
    """Collector adapter for Anthropic OAuth profiles on ``claude_code``."""

    runtime_id = RUNTIME_ID

    def supports(self, *, provider_id: str) -> bool:
        return provider_id == PROVIDER_ID

    async def request_from_owner(
        self, request: OwnerUsageRequest
    ) -> OwnerUsageReport | None:
        # An active claude-native session already forwards its status-line
        # quota. Only it may renew its credentials, so a request while it
        # runs defers rather than reading or refreshing a second time.
        return None

    def idle_probe(self, context: IdleProbeContext) -> UsageProbePlan:
        return UsageProbePlan(
            argv=("python3", "-c", PROBE_SOURCE, "read", context.credential_home_path),
            timeout_seconds=READ_TIMEOUT_SECONDS,
        )

    def recovery_probe(self, context: IdleProbeContext) -> UsageProbePlan:
        return UsageProbePlan(
            argv=(
                "python3",
                "-c",
                PROBE_SOURCE,
                "recover",
                context.credential_home_path,
            ),
            writes_credentials=True,
            timeout_seconds=RECOVERY_TIMEOUT_SECONDS,
        )

    def parse_probe(
        self, plan: UsageProbePlan, exit_code: int, stdout: bytes
    ) -> ProbeResult:
        return parse_probe_output(exit_code, stdout)


__all__ = [
    "PROVIDER_ID",
    "RUNTIME_ID",
    "SOURCE_HTTP",
    "SOURCE_PROFILE",
    "SOURCE_STATUS_LINE",
    "ClaudeUsageCollector",
    "identity_from_oauth_profile",
    "parse_probe_output",
    "status_line_report",
    "usage_from_oauth_usage",
]
