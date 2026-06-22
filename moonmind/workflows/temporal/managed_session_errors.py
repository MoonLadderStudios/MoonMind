"""Shared managed-session error classifiers."""

from __future__ import annotations

_MANAGED_SESSION_LOCATOR_MISMATCH_MARKERS = (
    "sessionid does not match the active managed session",
    "sessionepoch does not match the active managed session",
    "containerid does not match the active managed session",
    "threadid does not match the active managed session",
    "sessionepoch does not match the durable managed session record",
    "containerid does not match the durable managed session record",
    "threadid does not match the durable managed session record",
)


def is_managed_session_locator_mismatch_error(exc: BaseException) -> bool:
    """Return True when an exception chain reports a stale managed-session locator."""

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(getattr(current, "message", "") or current).strip().lower()
        if any(marker in message for marker in _MANAGED_SESSION_LOCATOR_MISMATCH_MARKERS):
            return True
        current = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        if not isinstance(current, BaseException):
            current = None
    return False


__all__ = ["is_managed_session_locator_mismatch_error"]
