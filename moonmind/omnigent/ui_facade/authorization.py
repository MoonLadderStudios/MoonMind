"""Caller authorization decisions for the browser-facing facade.

Pure authorization: given the caller's identity and the resource owner, decide
whether the caller may act. The facade never mutates session lifecycle; it only
gates access before delegating to an application use case.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str


def authorize_session_access(
    *, caller_id: str | None, owner_id: str | None, is_admin: bool = False
) -> AuthorizationDecision:
    """Allow access when the caller owns the session or is an admin."""

    if not caller_id:
        return AuthorizationDecision(False, "unauthenticated")
    if is_admin:
        return AuthorizationDecision(True, "admin")
    if owner_id is not None and caller_id == owner_id:
        return AuthorizationDecision(True, "owner")
    return AuthorizationDecision(False, "forbidden")


__all__ = ["AuthorizationDecision", "authorize_session_access"]
