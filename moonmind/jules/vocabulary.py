"""Canonical Jules provider wire vocabulary.

Adapter-owned single source of truth for Jules status classification.

Discrepancy table (reviewed 2026-09-08 for MoonMind#3952):

- ``moonmind/jules/status.py`` (snapshot, unknown-tolerant) accepted:
  pending/queued, running/in_progress/in-progress/processing, completed/success/
  done/resolved/finished, error/failed/rejected/timed_out/timeout,
  cancelled/canceled, awaiting_user_feedback. Blank/None defaulted to
  ``pending`` -> queued (fabricated queued state; fixed here to unknown).
- ``moonmind/schemas/jules_models.py`` (string, raising) accepted:
  accepted/assigned/created/open/submitted -> queued; awaiting_plan_approval/
  blocked/paused/planning/started/in_progress/running -> running;
  completed/done/finished/resolved/success -> completed; errored/failed/
  failure/timed_out/timeout -> failed; canceled/cancelled -> canceled;
  awaiting_user_feedback -> awaiting_feedback; state_unspecified -> unknown.
  Blank/None -> unknown. Unmapped tokens raised ``UnsupportedStatusError``.
- Canonical map below is the reviewed union of both sets: every token either
  helper accepted remains accepted with the same normalized outcome, plus
  hyphen/underscore/space-insensitive token normalization (``in-progress``,
  ``in progress`` and ``in_progress`` are one token). ``error``/``rejected``
  (snapshot-only) and ``errored``/``failure`` (schema-only) are retained as
  explicit failure aliases so historical payloads keep their meaning;
  ``processing``/``accepted``/``assigned``/``created``/``open``/``submitted``/
  ``blocked``/``paused``/``started``/``planning``/``awaiting_plan_approval``
  are retained as queued/running aliases for the same reason.
- ``state_unspecified`` and blank/None/whitespace classify as ``unknown``
  (never queued/running/completed). Unknown/malformed tokens classify as
  ``unknown`` with ``terminal=False``/``succeeded=False``; the
  execution-critical boundary :func:`require_known_jules_status` rejects them
  instead of interpreting them.

Separation of concerns: classification (this module) never performs lifecycle
transitions. Turn completion, session completion, verified AgentRun success,
artifact durability and publication authorization are decided by callers from
the classification result plus their own verified evidence. In particular a
provider pull-request URL is evidence exposed as metadata, never a promotion
from ``running`` to ``completed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NoReturn

from moonmind.schemas.agent_runtime_models import UnsupportedStatusError

JulesNormalizedStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
    "unknown",
    "awaiting_feedback",
]

JULES_TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "canceled"})

# Reviewed canonical wire map: normalized token -> normalized status.
# Tokens are matched after _normalize_token (strip, lowercase, hyphens and
# whitespace folded to underscores).
JULES_WIRE_STATUS_MAP: dict[str, JulesNormalizedStatus] = {
    # queued
    "accepted": "queued",
    "assigned": "queued",
    "created": "queued",
    "open": "queued",
    "pending": "queued",
    "queued": "queued",
    "submitted": "queued",
    # running
    "awaiting_plan_approval": "running",
    "blocked": "running",
    "in_progress": "running",
    "paused": "running",
    "planning": "running",
    "processing": "running",
    "running": "running",
    "started": "running",
    # completed
    "completed": "completed",
    "done": "completed",
    "finished": "completed",
    "resolved": "completed",
    "success": "completed",
    # failed
    "error": "failed",
    "errored": "failed",
    "failed": "failed",
    "failure": "failed",
    "rejected": "failed",
    "timed_out": "failed",
    "timeout": "failed",
    # canceled
    "canceled": "canceled",
    "cancelled": "canceled",
    # provider extension (adapter-owned, not a MoonMind lifecycle state)
    "awaiting_user_feedback": "awaiting_feedback",
    # explicit provider unknown sentinel
    "state_unspecified": "unknown",
}

# Bounded raw provenance: never log or persist arbitrary secret-bearing input.
_MAX_RAW_PROVENANCE_CHARS = 64

# Legacy grouped token sets, derived from the canonical map for back-compat
# imports (moonmind.jules.status re-exports these names).
JULES_SUCCESS_PROVIDER_STATUSES: frozenset[str] = frozenset(
    token for token, mapped in JULES_WIRE_STATUS_MAP.items() if mapped == "completed"
)
JULES_CANCELED_PROVIDER_STATUSES: frozenset[str] = frozenset({"cancelled", "canceled"})
JULES_FAILED_PROVIDER_STATUSES: frozenset[str] = frozenset(
    token for token, mapped in JULES_WIRE_STATUS_MAP.items() if mapped == "failed"
)
JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES = JULES_SUCCESS_PROVIDER_STATUSES
JULES_TERMINAL_FAILURE_PROVIDER_STATUSES: frozenset[str] = frozenset(
    {*JULES_CANCELED_PROVIDER_STATUSES, *JULES_FAILED_PROVIDER_STATUSES}
)
JULES_DEFAULT_PROVIDER_STATUS = "pending"


class JulesUnknownStatusError(UnsupportedStatusError):
    """Raised at execution-critical boundaries for unknown/missing Jules status."""


@dataclass(frozen=True, slots=True)
class JulesStatusClassification:
    """Display-safe Jules classification. Never raises for unknown input."""

    provider_status: str
    provider_status_token: str
    normalized_status: JulesNormalizedStatus
    terminal: bool
    succeeded: bool
    failed: bool
    canceled: bool
    is_known: bool
    is_missing: bool

    def require_known(self, context: str = "") -> JulesNormalizedStatus:
        """Return the normalized status or reject unknown/missing values."""
        if not self.is_known or self.normalized_status == "unknown":
            raise JulesUnknownStatusError(
                f"Unsupported status: {self.provider_status!r}"
                + (f" (context: {context})" if context else "")
            )
        return self.normalized_status


def _normalize_token(raw_status: Any) -> str:
    text = str(raw_status or "").strip().lower() if isinstance(raw_status, str) else ""
    if not text and raw_status is not None and not isinstance(raw_status, str):
        return ""
    token = text.replace("-", "_").replace(" ", "_")
    while "__" in token:
        token = token.replace("__", "_")
    return token


def _bounded_provenance(raw_status: Any) -> str:
    if raw_status is None:
        return "unknown"
    text = str(raw_status).strip() or "unknown"
    return text[:_MAX_RAW_PROVENANCE_CHARS]


def classify_jules_status(raw_status: Any) -> JulesStatusClassification:
    """Classify one raw Jules status value. Never raises.

    Blank/None/whitespace, unrecognized tokens and non-string input all
    classify as ``unknown`` with ``terminal=False`` and ``succeeded=False``,
    so unknown values can never imply success, authorize publication, or
    fabricate a queued/running state.
    """
    if not isinstance(raw_status, str):
        provider_status = "unknown" if raw_status is None else _bounded_provenance(raw_status)
        token = _normalize_token(raw_status) if isinstance(raw_status, str) else ""
        if raw_status is not None and token and token in JULES_WIRE_STATUS_MAP:
            normalized = JULES_WIRE_STATUS_MAP[token]
        else:
            normalized = "unknown"
            token = token if isinstance(raw_status, str) else ""
        is_missing = raw_status is None or (isinstance(raw_status, str) and not raw_status.strip())
        # Non-string non-None input is malformed, not missing.
        if raw_status is not None and not isinstance(raw_status, str):
            is_missing = False
            provider_status = _bounded_provenance(raw_status)
        return JulesStatusClassification(
            provider_status=provider_status,
            provider_status_token=token,
            normalized_status=normalized,  # type: ignore[arg-type]
            terminal=normalized in JULES_TERMINAL_STATUSES,
            succeeded=normalized == "completed",
            failed=normalized == "failed",
            canceled=normalized == "canceled",
            is_known=normalized != "unknown",
            is_missing=is_missing,
        )
    token = _normalize_token(raw_status)
    if not token:
        return JulesStatusClassification(
            provider_status="unknown",
            provider_status_token="",
            normalized_status="unknown",
            terminal=False,
            succeeded=False,
            failed=False,
            canceled=False,
            is_known=False,
            is_missing=True,
        )
    normalized = JULES_WIRE_STATUS_MAP.get(token)
    if normalized is None:
        return JulesStatusClassification(
            provider_status=_bounded_provenance(raw_status),
            provider_status_token=token,
            normalized_status="unknown",
            terminal=False,
            succeeded=False,
            failed=False,
            canceled=False,
            is_known=False,
            is_missing=False,
        )
    return JulesStatusClassification(
        provider_status=_bounded_provenance(raw_status),
        provider_status_token=token,
        normalized_status=normalized,
        terminal=normalized in JULES_TERMINAL_STATUSES,
        succeeded=normalized == "completed",
        failed=normalized == "failed",
        canceled=normalized == "canceled",
        is_known=normalized != "unknown",
        is_missing=False,
    )


def require_known_jules_status(raw_status: Any, context: str = "") -> JulesNormalizedStatus:
    """Execution-critical boundary: return normalized status or raise."""
    return classify_jules_status(raw_status).require_known(context)


def raise_unsupported_jules_status(raw_status: Any, context: str = "") -> NoReturn:
    """Raise for unknown/missing status, mirroring the legacy helper name."""
    classify_jules_status(raw_status).require_known(context)
    raise AssertionError("unreachable")  # pragma: no cover


def is_jules_terminal_status(normalized: str) -> bool:
    """Return whether a normalized Jules status is terminal."""
    return normalized in JULES_TERMINAL_STATUSES


__all__ = [
    "JULES_CANCELED_PROVIDER_STATUSES",
    "JULES_DEFAULT_PROVIDER_STATUS",
    "JULES_FAILED_PROVIDER_STATUSES",
    "JULES_SUCCESS_PROVIDER_STATUSES",
    "JULES_TERMINAL_FAILURE_PROVIDER_STATUSES",
    "JULES_TERMINAL_SUCCESS_PROVIDER_STATUSES",
    "JULES_TERMINAL_STATUSES",
    "JULES_WIRE_STATUS_MAP",
    "JulesNormalizedStatus",
    "JulesStatusClassification",
    "JulesUnknownStatusError",
    "classify_jules_status",
    "is_jules_terminal_status",
    "raise_unsupported_jules_status",
    "require_known_jules_status",
]
