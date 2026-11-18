"""Normalized provider account-usage contract (schema v1).

Provider collectors translate their native or HTTP responses into these
allowlisted models; :mod:`api_service.services.provider_account_usage`
fences, merges and caches them as the latest useful snapshot per Provider
Profile. Every model forbids unknown fields so raw provider bodies, tokens or
headers cannot ride along, and free-text values that look like credentials are
rejected rather than stored.

Identity, each usage window and refresh state are independent. Observation
times always come from the observation itself, never from receipt, so
re-delivered cached telemetry keeps its original time and a late observation
cannot replace a newer accepted one.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from enum import StrEnum
from random import Random
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.utils.logging import redact_sensitive_text

ACCOUNT_USAGE_SCHEMA_VERSION = 1

#: A reading older than this is stale, and visibility-driven refreshes
#: collect again once the last completed attempt is this old.
STALE_AFTER = timedelta(minutes=5)
#: One manual refresh per verified account (or profile binding) per minute.
MANUAL_REFRESH_INTERVAL = timedelta(minutes=1)
#: Queued/running attempts past this deadline are abandoned and reclaimable.
ATTEMPT_DEADLINE = timedelta(minutes=2)
#: A busy profile is not probed again before this delay.
BUSY_RETRY_AFTER = timedelta(minutes=1)
MIN_BACKOFF = timedelta(minutes=1)
MAX_BACKOFF = timedelta(hours=1)
BACKOFF_JITTER_RATIO = 0.2
#: Automatic triggers stop collecting after this many consecutive failures
#: until a manual refresh or a successful observation.
MAX_AUTOMATIC_FAILURES = 5
MAX_WINDOWS = 32

_KEY_PATTERN = r"^[a-z0-9][a-z0-9_.:-]{0,63}$"
_SOURCE_PATTERN = r"^[a-z0-9][a-z0-9_.:-]{0,63}$"
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:@/+-]{0,127}$"
_EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")
_SECRET_SHAPED = re.compile(
    r"(?i)(?:\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}|\bbearer\s|"
    r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}|\bsess-[A-Za-z0-9]{8,})"
)
_DECIMAL_STRING = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")


class AccountUsageNormalizationError(ValueError):
    """A provider value could not be normalized into the contract."""


class UsageWindowPeriod(StrEnum):
    WEEKLY = "weekly"
    SHORT = "short"
    OTHER = "other"


class UsageWindowScope(StrEnum):
    GENERAL = "general"
    MODEL = "model"


class UsageCompleteness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class WindowFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"


class IdentityStatus(StrEnum):
    UNKNOWN = "unknown"
    REPORTED = "reported"
    VERIFIED = "verified"


class AccountUsageStatus(StrEnum):
    """Safe observation states, distinct from refresh-operation state."""

    UNKNOWN = "unknown"
    FRESH = "fresh"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    NOT_REPORTED = "not_reported"
    NOT_SUPPORTED = "not_supported"
    USAGE_ACCESS_MISSING = "usage_access_missing"
    RECONNECT_REQUIRED = "reconnect_required"


class RefreshState(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    DEFERRED = "deferred"
    BACKOFF = "backoff"


class RefreshTrigger(StrEnum):
    MANUAL = "manual"
    VISIBILITY = "visibility"
    OAUTH = "oauth"
    NATIVE_HINT = "native_hint"

    @property
    def automatic(self) -> bool:
        return self is not RefreshTrigger.MANUAL


class RefreshOutcome(StrEnum):
    ALREADY_FRESH = "already_fresh"
    ACCEPTED = "accepted"
    DEFERRED_BUSY = "deferred_busy"
    BACKOFF = "backoff"
    NOT_SUPPORTED = "not_supported"
    NOT_CONNECTED = "not_connected"
    DISABLED = "disabled"


class AccountUsageFailureCode(StrEnum):
    """Typed, secret-free collection failures. Provider text is never kept."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    USAGE_ACCESS_MISSING = "usage_access_missing"
    RECONNECT_REQUIRED = "reconnect_required"
    DISPATCH_FAILED = "dispatch_failed"
    #: The stored access token expired and no supported native recovery
    #: action could renew it while idle. This does not claim the login is
    #: invalid; that is decided only by the authentication lifecycle.
    CREDENTIAL_REFRESH_UNAVAILABLE = "credential_refresh_unavailable"
    #: The installed runtime has no native idle recovery action; updating
    #: the runtime or reconnecting the profile restores idle reads.
    CREDENTIAL_RECOVERY_UNSUPPORTED = "credential_recovery_unsupported"
    ATTEMPT_ABANDONED = "attempt_abandoned"
    INTERNAL_ERROR = "internal_error"

    @property
    def temporary(self) -> bool:
        return self not in {
            AccountUsageFailureCode.USAGE_ACCESS_MISSING,
            AccountUsageFailureCode.RECONNECT_REQUIRED,
        }


class ObservationRejection(StrEnum):
    STALE_GENERATION = "stale_generation"
    ACCOUNT_MISMATCH = "account_mismatch"
    OWNER_EXPIRED = "owner_expired"
    OUT_OF_ORDER = "out_of_order"
    PROFILE_MISSING = "profile_missing"


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------


def normalize_used_percent(value: Any) -> float | None:
    """Return a finite, non-negative percentage used (``1`` means 1%).

    ``None`` stays unknown. Booleans, NaN/infinity, negative values and
    strings that are not plain decimals are rejected; values above 100 are
    valid overage.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise AccountUsageNormalizationError("used percentage must be numeric")
    if isinstance(value, str):
        text = value.strip()
        if not _DECIMAL_STRING.fullmatch(text):
            raise AccountUsageNormalizationError("used percentage is malformed")
        try:
            number = float(Decimal(text))
        except (InvalidOperation, OverflowError) as exc:
            raise AccountUsageNormalizationError(
                "used percentage is malformed"
            ) from exc
    elif isinstance(value, (int, float, Decimal)):
        try:
            number = float(value)
        except OverflowError as exc:
            raise AccountUsageNormalizationError(
                "used percentage is not finite"
            ) from exc
    else:
        raise AccountUsageNormalizationError("used percentage must be numeric")
    if not math.isfinite(number):
        raise AccountUsageNormalizationError("used percentage is not finite")
    if number < 0:
        raise AccountUsageNormalizationError("used percentage cannot be negative")
    return number


def remaining_percent(used_percent: float | None) -> float | None:
    """Displayed remaining allowance, clamped to 0..100 for valid overage."""

    if used_percent is None:
        return None
    return max(0.0, min(100.0, 100.0 - used_percent))


def normalize_instant(value: Any) -> datetime | None:
    """Return an absolute UTC instant from an aware datetime, ISO text or epoch."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise AccountUsageNormalizationError("timestamp must be an instant")
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or value < 0:
            raise AccountUsageNormalizationError("epoch timestamp is invalid")
        try:
            instant = datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise AccountUsageNormalizationError("epoch timestamp is invalid") from exc
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            instant = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AccountUsageNormalizationError("timestamp is malformed") from exc
    else:
        raise AccountUsageNormalizationError("timestamp must be an instant")
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise AccountUsageNormalizationError("timestamp must include a UTC offset")
    return instant.astimezone(UTC)


def parse_retry_after(value: Any, *, now: datetime) -> timedelta | None:
    """Parse ``Retry-After`` delta-seconds or HTTP-date; invalid yields ``None``."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            seconds = float(text)
        else:
            try:
                instant = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                return None
            if instant is None:
                return None
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            return max(timedelta(0), instant - now)
    else:
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return timedelta(seconds=seconds)


def backoff_delay(
    *,
    consecutive_failures: int,
    retry_after: timedelta | None,
    rng: Random,
) -> timedelta:
    """Exponential backoff with a floor, the provider's longer delay, and jitter."""

    exponent = max(0, consecutive_failures - 1)
    base = min(MAX_BACKOFF, MIN_BACKOFF * (2 ** min(exponent, 16)))
    delay = max(MIN_BACKOFF, base, retry_after or timedelta(0))
    jitter = delay.total_seconds() * BACKOFF_JITTER_RATIO * rng.random()
    return delay + timedelta(seconds=jitter)


def window_fresh_until(observed_at: datetime, reset_at: datetime | None) -> datetime:
    """Freshness expires at the earlier of the TTL or the known reset."""

    expiry = observed_at + STALE_AFTER
    return min(expiry, reset_at) if reset_at is not None else expiry


def window_freshness(
    *, observed_at: datetime, reset_at: datetime | None, now: datetime
) -> WindowFreshness:
    if reset_at is not None and now >= reset_at:
        return WindowFreshness.EXPIRED
    if now < window_fresh_until(observed_at, reset_at):
        return WindowFreshness.FRESH
    return WindowFreshness.STALE


def _safe_text(value: Any, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be text")
    text = value.strip()
    if not text:
        return None
    if len(text) > max_length or not text.isprintable():
        raise ValueError("is not a safe display value")
    if _SECRET_SHAPED.search(text) or redact_sensitive_text(text) != text:
        raise ValueError("looks like credential material")
    return text


# ---------------------------------------------------------------------------
# Collector input models
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class UsageWindowInput(_Strict):
    """One provider-reported allowance window."""

    key: str = Field(pattern=_KEY_PATTERN)
    period: UsageWindowPeriod
    scope: UsageWindowScope = UsageWindowScope.GENERAL
    model_label: str | None = None
    duration_seconds: int | None = Field(default=None, gt=0, le=366 * 86400)
    used_percent: float | None = None
    used_unit: Literal["percent"] = "percent"
    reset_at: datetime | None = None
    source: str = Field(pattern=_SOURCE_PATTERN)
    source_version: str | None = Field(default=None, max_length=64)
    observed_at: datetime | None = None

    @field_validator("used_percent", mode="before")
    @classmethod
    def _used(cls, value: Any) -> float | None:
        return normalize_used_percent(value)

    @field_validator("reset_at", "observed_at", mode="before")
    @classmethod
    def _instant(cls, value: Any) -> datetime | None:
        return normalize_instant(value)

    @field_validator("model_label", mode="before")
    @classmethod
    def _model_label(cls, value: Any) -> str | None:
        return _safe_text(value, max_length=128)

    @field_validator("source_version", mode="before")
    @classmethod
    def _source_version(cls, value: Any) -> str | None:
        return _safe_text(value, max_length=64)

    @model_validator(mode="after")
    def _scope_label(self) -> "UsageWindowInput":
        if self.scope is UsageWindowScope.MODEL and not self.model_label:
            raise ValueError("model-scoped windows require model_label")
        if self.scope is UsageWindowScope.GENERAL and self.model_label:
            raise ValueError("general windows cannot carry a model_label")
        return self


class UsageInput(_Strict):
    completeness: UsageCompleteness
    observed_at: datetime
    windows: tuple[UsageWindowInput, ...] = Field(default=(), max_length=MAX_WINDOWS)
    primary_window_key: str | None = Field(default=None, pattern=_KEY_PATTERN)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _instant(cls, value: Any) -> datetime | None:
        instant = normalize_instant(value)
        if instant is None:
            raise ValueError("observed_at is required")
        return instant

    @model_validator(mode="after")
    def _windows(self) -> "UsageInput":
        keys = [window.key for window in self.windows]
        if len(keys) != len(set(keys)):
            raise ValueError("window keys must be unique")
        if self.primary_window_key is not None:
            primary = next(
                (w for w in self.windows if w.key == self.primary_window_key), None
            )
            # A model-scoped or short window is never the general allowance.
            if primary is None or not _is_general_weekly(primary):
                raise ValueError("primary_window_key must name a general weekly window")
        return self


class AccountIdentityInput(_Strict):
    email: str | None = None
    account_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    workspace_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    display_name: str | None = None
    plan: str | None = None
    #: True only when account/workspace IDs came from the provider's
    #: authenticated identity for these credentials.
    verified: bool = False
    source: str = Field(pattern=_SOURCE_PATTERN)
    observed_at: datetime

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value: Any) -> str | None:
        text = _safe_text(value, max_length=320)
        if text is not None and not _EMAIL_PATTERN.fullmatch(text):
            raise ValueError("email is malformed")
        return text

    @field_validator("display_name", mode="before")
    @classmethod
    def _display_name(cls, value: Any) -> str | None:
        return _safe_text(value, max_length=128)

    @field_validator("plan", mode="before")
    @classmethod
    def _plan(cls, value: Any) -> str | None:
        return _safe_text(value, max_length=64)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _instant(cls, value: Any) -> datetime | None:
        instant = normalize_instant(value)
        if instant is None:
            raise ValueError("observed_at is required")
        return instant

    @model_validator(mode="after")
    def _verified_needs_account(self) -> "AccountIdentityInput":
        if self.verified and not self.account_id:
            raise ValueError("verified identity requires account_id")
        return self


class ObservationAuthority(_Strict):
    """Who may publish: the claimed refresh attempt or the runtime owner."""

    kind: Literal["refresh_attempt", "native_owner"]
    attempt_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    owner_expires_at: datetime | None = None

    @field_validator("owner_expires_at", mode="before")
    @classmethod
    def _instant(cls, value: Any) -> datetime | None:
        return normalize_instant(value)

    @model_validator(mode="after")
    def _shape(self) -> "ObservationAuthority":
        if self.kind == "refresh_attempt" and not self.attempt_id:
            raise ValueError("refresh_attempt authority requires attempt_id")
        if self.kind == "native_owner" and self.owner_expires_at is None:
            raise ValueError("native_owner authority requires owner_expires_at")
        return self


class AccountUsageObservation(_Strict):
    """One collector publication for one profile credential binding."""

    schema_version: Literal[1] = ACCOUNT_USAGE_SCHEMA_VERSION
    profile_id: str = Field(min_length=1, max_length=128)
    credential_generation: int = Field(ge=1)
    provider_id: str = Field(min_length=1, max_length=64)
    runtime_id: str = Field(min_length=1, max_length=64)
    authority: ObservationAuthority
    #: Trusted account/workspace binding of the credentials that were read.
    account_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    workspace_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    identity: AccountIdentityInput | None = None
    usage: UsageInput | None = None


# ---------------------------------------------------------------------------
# Stored snapshot and merge semantics
# ---------------------------------------------------------------------------


def _is_general_weekly(window: UsageWindowInput | Mapping[str, Any]) -> bool:
    if isinstance(window, Mapping):
        scope, period = window.get("scope"), window.get("period")
    else:
        scope, period = window.scope, window.period
    return scope == UsageWindowScope.GENERAL and period == UsageWindowPeriod.WEEKLY


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def parse_stored_instant(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def _stored_window(window: UsageWindowInput, observed_at: datetime) -> dict[str, Any]:
    return {
        "key": window.key,
        "period": window.period.value,
        "scope": window.scope.value,
        "model_label": window.model_label,
        "duration_seconds": window.duration_seconds,
        "used_percent": window.used_percent,
        "reset_at": _iso(window.reset_at),
        "source": window.source,
        "source_version": window.source_version,
        "observed_at": _iso(window.observed_at or observed_at),
    }


def _derived_primary(windows: Mapping[str, Mapping[str, Any]]) -> str | None:
    general_weekly = [key for key, w in windows.items() if _is_general_weekly(w)]
    return general_weekly[0] if len(general_weekly) == 1 else None


@dataclass(frozen=True)
class UsageMerge:
    usage: dict[str, Any] | None
    changed: bool
    rejection: ObservationRejection | None = None


def merge_usage(stored: Mapping[str, Any] | None, incoming: UsageInput) -> UsageMerge:
    """Merge one usage observation into the stored latest snapshot.

    A full snapshot replaces the window set: windows it omits are removed
    unless a newer partial observation updated them after the snapshot was
    taken. A partial observation only updates the windows it carries, and
    cannot restore a window that a newer full snapshot omitted. A window is
    replaced only by a strictly newer reading; an older full snapshot is
    rejected as out of order.
    """

    current = dict(stored or {})
    windows: dict[str, dict[str, Any]] = {
        key: dict(value) for key, value in (current.get("windows") or {}).items()
    }
    full_at = parse_stored_instant(current.get("full_observed_at"))
    full = incoming.completeness is UsageCompleteness.FULL
    if full and full_at is not None and incoming.observed_at < full_at:
        return UsageMerge(
            usage=None, changed=False, rejection=ObservationRejection.OUT_OF_ORDER
        )

    changed = False
    incoming_keys = set()
    for window in incoming.windows:
        candidate = _stored_window(window, incoming.observed_at)
        incoming_keys.add(window.key)
        existing = windows.get(window.key)
        candidate_at = parse_stored_instant(candidate["observed_at"])
        if existing is not None:
            existing_at = parse_stored_instant(existing["observed_at"])
            if (
                candidate_at is None
                or existing_at is None
                or candidate_at <= existing_at
            ):
                continue
        elif (
            not full
            and full_at is not None
            and (candidate_at is None or candidate_at <= full_at)
        ):
            continue
        windows[window.key] = candidate
        changed = True

    if full:
        for key in list(windows):
            if key in incoming_keys:
                continue
            window_at = parse_stored_instant(windows[key]["observed_at"])
            if window_at is not None and window_at <= incoming.observed_at:
                del windows[key]
                changed = True
        primary = incoming.primary_window_key or _derived_primary(
            {key: windows[key] for key in incoming_keys if key in windows}
        )
        updates = {
            "completeness": UsageCompleteness.FULL.value,
            "full_observed_at": _iso(incoming.observed_at),
            "primary_window_key": primary if primary in windows else None,
            "reported": bool(incoming.windows),
        }
        for key, value in updates.items():
            if current.get(key) != value:
                current[key] = value
                changed = True
    elif "completeness" not in current and incoming.windows:
        current.update(
            completeness=UsageCompleteness.PARTIAL.value,
            full_observed_at=None,
            primary_window_key=None,
            reported=True,
        )
        changed = True
    if current.get("primary_window_key") not in windows:
        current["primary_window_key"] = None
    current["windows"] = windows
    return UsageMerge(usage=current, changed=changed)


def merge_identity(
    stored: Mapping[str, Any] | None, incoming: AccountIdentityInput
) -> tuple[dict[str, Any] | None, bool]:
    """Replace identity only with a strictly newer observation."""

    stored_at = parse_stored_instant((stored or {}).get("observed_at"))
    if stored_at is not None and incoming.observed_at <= stored_at:
        return (dict(stored) if stored else None), False
    if incoming.verified:
        status = IdentityStatus.VERIFIED
    elif incoming.email or incoming.display_name or incoming.account_id:
        status = IdentityStatus.REPORTED
    else:
        status = IdentityStatus.UNKNOWN
    return (
        {
            "status": status.value,
            "email": incoming.email,
            "display_name": incoming.display_name,
            "plan": incoming.plan,
            "account_id": incoming.account_id,
            "workspace_id": incoming.workspace_id,
            "verified": incoming.verified,
            "source": incoming.source,
            "observed_at": _iso(incoming.observed_at),
        },
        True,
    )
