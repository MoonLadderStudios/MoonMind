"""Sanitized diagnostics for managed runtime isolation outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

_ALLOWED_REASON_CODES = frozenset(
    {
        "egress_blocked",
        "surface_rejected",
        "direct_publish_denied",
        "pull_request_adopted",
        "publish_lease_conflict",
    }
)


@dataclass(frozen=True, slots=True)
class IsolationDiagnostic:
    """Operator-visible evidence for an isolation or publish boundary event."""

    reason_code: str
    summary: str
    surface: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(tz=UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    def __post_init__(self) -> None:
        if self.reason_code not in _ALLOWED_REASON_CODES:
            raise ValueError(f"unsupported isolation diagnostic reason: {self.reason_code}")

    @staticmethod
    def allowed_reason_codes() -> frozenset[str]:
        return _ALLOWED_REASON_CODES

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reasonCode": self.reason_code,
            "summary": redact_sensitive_text(self.summary),
            "createdAt": self.created_at,
            "metadata": redact_sensitive_payload(dict(self.metadata)),
        }
        if self.surface:
            payload["surface"] = redact_sensitive_text(self.surface)
        return payload


def build_isolation_diagnostic(
    *,
    reason_code: str,
    summary: str,
    surface: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> IsolationDiagnostic:
    return IsolationDiagnostic(
        reason_code=reason_code,
        summary=summary,
        surface=surface,
        metadata=dict(metadata or {}),
    )


__all__ = ["IsolationDiagnostic", "build_isolation_diagnostic"]
