"""Codex OAuth account-usage collection (MoonLadderStudios/MoonMind#5364).

OpenAI OAuth profiles on ``codex_cli`` read account and quota through Codex's
own app-server protocol: ``account/read`` (email, plan, workspace, and the
ChatGPT account from workspace routing) and ``account/rateLimits/read``
(``rateLimitsByLimitId``, with the legacy single ``rateLimits`` bucket as
fallback, and the usage ``accountId``). Two owners send it:

* **Native owner.** The Omnigent native Codex forwarder reads the pair on the
  session's existing app-server connection after it starts and when Codex
  signals ``account/rateLimits/updated``, and relays an allowlisted
  projection on ``session.usage``. MoonMind binds it to the step's held lease
  (:mod:`moonmind.omnigent.native_account_usage`).
* **Idle probe.** :class:`CodexUsageCollector` runs
  :mod:`codex_account_usage_probe` in the installed host image under the
  exclusive usage-observation grant.

Both send the same projection, translated here into one full snapshot. A
window is weekly only when its duration is 10,080 minutes, in either slot.
The general pool is the ``codex`` limit and supplies the headline; a pool
with an explicit model is model-scoped, and any other pool is reported as
another limit. Pools are never summed or substituted. Resets are Unix seconds.
A recognized field with an unreadable value fails the reading rather than
being dropped, so cached values keep their times.
"""

from __future__ import annotations

import json
import re
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

RUNTIME_ID = "codex_cli"
PROVIDER_ID = "openai"
SOURCE_RATE_LIMITS = "codex_rate_limits"
SOURCE_ACCOUNT = "codex_account"
GENERAL_LIMIT_ID = "codex"

_WEEK_MINUTES = 7 * 24 * 60
_SLUG = re.compile(r"[^a-z0-9_.:-]+")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,31}$")
_MAX_WINDOWS = 32

#: Probe source shipped to ``python3 -c``; MoonMind is not in the host image.
PROBE_SOURCE = (
    Path(__file__).with_name("codex_account_usage_probe.py").read_text(encoding="utf-8")
)
READ_TIMEOUT_SECONDS = 60
#: Covers a forced renewal and the read that follows it.
RECOVERY_TIMEOUT_SECONDS = 90


class MalformedCodexUsage(Exception):
    """A recognized field held a value that is not a percentage or instant."""


def _percent(value: Any) -> float | None:
    try:
        return normalize_used_percent(value)
    except AccountUsageNormalizationError as exc:
        raise MalformedCodexUsage from exc


def _instant(value: Any) -> Any:
    """An instant from Unix seconds; text and other shapes are unreadable."""

    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise MalformedCodexUsage
    try:
        return normalize_instant(value)
    except AccountUsageNormalizationError as exc:
        raise MalformedCodexUsage from exc


def _minutes(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MalformedCodexUsage
    return value


def _duration_label(minutes: int | None, slot: str) -> str:
    if minutes is None:
        return slot
    if minutes == _WEEK_MINUTES:
        return "weekly"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _period(minutes: int | None, *, pool: str) -> UsageWindowPeriod:
    if pool == "other" or minutes is None or minutes > _WEEK_MINUTES:
        return UsageWindowPeriod.OTHER
    if minutes == _WEEK_MINUTES:
        return UsageWindowPeriod.WEEKLY
    return UsageWindowPeriod.SHORT


def _bucket_windows(
    limit_id: str,
    bucket: Any,
    *,
    observed_at: Any,
    source_version: str | None,
) -> list[UsageWindowInput]:
    if bucket is None:
        return []
    if not isinstance(bucket, Mapping):
        raise MalformedCodexUsage
    slug = _SLUG.sub("_", limit_id.lower()).strip("_.:-")[:40]
    if not slug:
        return []
    model = bucket.get("model")
    label = bucket.get("limit_name") or model
    if limit_id == GENERAL_LIMIT_ID:
        pool, model_label = "general", None
    elif model:
        pool, model_label = "model", label
    else:
        # Another metered pool: neither the general allowance nor a model.
        pool, model_label = "other", None
    windows = []
    for slot in ("primary", "secondary"):
        raw = bucket.get(slot)
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            raise MalformedCodexUsage
        minutes = _minutes(raw.get("window_duration_mins"))
        used, reset = _percent(raw.get("used_percent")), _instant(raw.get("resets_at"))
        if used is None and reset is None:
            continue
        try:
            windows.append(
                UsageWindowInput(
                    key=f"{slug}.{_duration_label(minutes, slot)}",
                    period=_period(minutes, pool=pool),
                    scope=(
                        UsageWindowScope.MODEL
                        if model_label
                        else UsageWindowScope.GENERAL
                    ),
                    model_label=model_label,
                    duration_seconds=minutes * 60 if minutes else None,
                    used_percent=used,
                    reset_at=reset,
                    source=SOURCE_RATE_LIMITS,
                    source_version=source_version,
                    observed_at=observed_at,
                )
            )
        except ValidationError as exc:
            raise MalformedCodexUsage from exc
    return windows


def _usage(projected: Mapping[str, Any], observed_at: Any) -> UsageInput:
    version = projected.get("codex_version")
    version = version if isinstance(version, str) and _VERSION.match(version) else None
    by_limit = projected.get("rate_limits_by_limit_id")
    if by_limit is not None and not isinstance(by_limit, Mapping):
        raise MalformedCodexUsage
    if by_limit:
        buckets = list(by_limit.items())
    else:
        legacy = projected.get("rate_limits")
        legacy_id = legacy.get("limit_id") if isinstance(legacy, Mapping) else None
        buckets = [(legacy_id or GENERAL_LIMIT_ID, legacy)]
    # The general pool first, so the cap never drops the headline.
    buckets.sort(key=lambda item: item[0] != GENERAL_LIMIT_ID)
    windows: dict[str, UsageWindowInput] = {}
    for limit_id, bucket in buckets:
        if not isinstance(limit_id, str):
            raise MalformedCodexUsage
        for window in _bucket_windows(
            limit_id, bucket, observed_at=observed_at, source_version=version
        ):
            windows.setdefault(window.key, window)
    primary = f"{GENERAL_LIMIT_ID}.weekly"
    try:
        return UsageInput(
            completeness=UsageCompleteness.FULL,
            observed_at=observed_at,
            windows=tuple(windows.values())[:_MAX_WINDOWS],
            primary_window_key=primary if primary in windows else None,
        )
    except ValidationError as exc:
        raise MalformedCodexUsage from exc


def _identity(
    projected: Mapping[str, Any], account_id: str | None, observed_at: Any
) -> AccountIdentityInput | None:
    account = projected.get("account")
    if not isinstance(account, Mapping):
        return None
    routed = projected.get("routing_account_id") or account_id
    values: dict[str, Any] = {}
    for field, value in (
        ("account_id", routed),
        ("email", account.get("email")),
        ("plan", account.get("plan_type")),
        ("display_name", account.get("workspace_name")),
    ):
        if value is None:
            continue
        try:
            AccountIdentityInput(
                **{field: value}, source=SOURCE_ACCOUNT, observed_at=observed_at
            )
        except ValidationError:
            if field == "account_id":
                raise MalformedCodexUsage from None
            continue
        values[field] = value
    return AccountIdentityInput(
        **values,
        verified=bool(values.get("account_id")),
        source=SOURCE_ACCOUNT,
        observed_at=observed_at,
    )


def report_from_projection(projected: Any) -> OwnerUsageReport:
    """One full snapshot from a probe or native-owner projection.

    The usage ``accountId`` binds the observation; the ChatGPT account from
    workspace routing binds the identity. When both are present and differ,
    the observation carries both and the service rejects it as an account
    mismatch.

    :raises MalformedCodexUsage: A recognized field held an unreadable value.
    """

    if not isinstance(projected, Mapping):
        raise MalformedCodexUsage
    observed_at = _instant(projected.get("observed_at"))
    if observed_at is None:
        raise MalformedCodexUsage
    account_id = projected.get("account_id")
    if account_id is not None and not isinstance(account_id, str):
        raise MalformedCodexUsage
    usage = _usage(projected, observed_at)
    try:
        return OwnerUsageReport(
            account_id=account_id,
            identity=_identity(projected, account_id, observed_at),
            usage=usage,
        )
    except ValidationError as exc:
        raise MalformedCodexUsage from exc


def native_report(projected: Any) -> OwnerUsageReport | None:
    """Report from a native owner's ``codex_account_usage`` event, or ``None``."""

    try:
        return report_from_projection(projected)
    except MalformedCodexUsage:
        return None


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
        return ProbeResult(failure=failure or AccountUsageFailureCode.INTERNAL_ERROR)
    try:
        report = report_from_projection(payload.get("usage"))
    except MalformedCodexUsage:
        return ProbeResult(failure=AccountUsageFailureCode.MALFORMED_RESPONSE)
    return ProbeResult(report=report)


class CodexUsageCollector:
    """Collector adapter for OpenAI OAuth profiles on ``codex_cli``."""

    runtime_id = RUNTIME_ID

    def supports(self, *, provider_id: str) -> bool:
        return provider_id == PROVIDER_ID

    async def request_from_owner(
        self, request: OwnerUsageRequest
    ) -> OwnerUsageReport | None:
        # A running native Codex session reads on its own app-server
        # connection and publishes through its lease; only it may renew its
        # credentials, so a request while it runs defers to it.
        return None

    def idle_probe(self, context: IdleProbeContext) -> UsageProbePlan:
        # Codex renews an expired access token as its app-server starts and
        # rotates the refresh token; a read-only home would lose the rotation
        # (and Codex cannot start without writing its state there).
        return UsageProbePlan(
            argv=("python3", "-c", PROBE_SOURCE, "read", context.credential_home_path),
            writes_credentials=True,
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
    "GENERAL_LIMIT_ID",
    "PROVIDER_ID",
    "RUNTIME_ID",
    "SOURCE_ACCOUNT",
    "SOURCE_RATE_LIMITS",
    "CodexUsageCollector",
    "MalformedCodexUsage",
    "native_report",
    "parse_probe_output",
    "report_from_projection",
]
