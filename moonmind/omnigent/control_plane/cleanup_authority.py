"""The one durable cleanup claim every Omnigent teardown owner must hold.

Source: MoonLadderStudios/MoonMind#3707 §4.

An admitted canonical turn advances the cleanup generation
(:meth:`CleanupAuthorityRepository.fence_for_turn`) so an outstanding janitor is
fenced out at completion. That fence is only real if the owners that actually
release hosts, credentials, and provider sessions claim *this* aggregate. The
legacy session supervisor and the generic Omnigent host realizer therefore share
this service instead of fencing only their own host-lease or OAuth-host rows.

The claim token is derived, not supplied: one canonical session has one cleanup
owner per owner class, so a retry of the same teardown resumes its own claim
idempotently while a second owner is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .records import APPLIED_OUTCOMES, ControlPlaneOutcome, compute_digest


@dataclass(frozen=True, slots=True)
class CanonicalCleanupClaim:
    """Exclusive, fenced authority to release one session's runtime resources."""

    session_id: str
    owner_class: str
    claim_token: str
    generation: int


def cleanup_claim_token(session_id: str, owner_class: str) -> str:
    """Return the stable claim identity for one owner class of one session."""

    return "ocl_" + compute_digest([session_id, owner_class, "cleanup"])[:40]


class CanonicalCleanupAuthority:
    """Claim and complete cleanup through the canonical session aggregate."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def claim(
        self, session_id: str, *, owner_class: str
    ) -> CanonicalCleanupClaim | None:
        """Return exclusive cleanup authority, or ``None`` when another owner won.

        ``None`` means this owner must not release the session's host,
        credentials, or provider session: either a live claim belongs to another
        janitor, or cleanup is already complete.
        """

        if not session_id:
            return None
        claim_token = cleanup_claim_token(session_id, owner_class)
        async with self._store.transaction() as repos:
            result = await repos.cleanup.claim_cleanup(
                session_id, owner_class=owner_class, claim_token=claim_token
            )
        if result.outcome is not ControlPlaneOutcome.APPLIED:
            return None
        return CanonicalCleanupClaim(
            session_id=session_id,
            owner_class=owner_class,
            claim_token=claim_token,
            generation=result.record.generation,
        )

    async def resolve_session_id(self, provider_session_ref: str) -> str:
        """Return the canonical session a provider session is attached to."""

        if not provider_session_ref:
            return ""
        async with self._store.transaction() as repos:
            session = await repos.sessions.get_by_provider_session(
                provider_session_ref
            )
        return session.session_id if session is not None else ""

    async def complete(self, claim: CanonicalCleanupClaim) -> bool:
        """Settle a held claim, or report that a newer turn fenced it out.

        ``False`` means an admitted turn (or a fenced takeover) advanced the
        cleanup generation after this owner claimed, so the session belongs to a
        newer generation and this owner must not report cleanup complete.
        """

        async with self._store.transaction() as repos:
            result = await repos.cleanup.complete_cleanup(
                claim.session_id,
                generation=claim.generation,
                owner_class=claim.owner_class,
                claim_token=claim.claim_token,
                session_repository=repos.sessions,
            )
        return result.outcome in APPLIED_OUTCOMES


__all__ = [
    "CanonicalCleanupAuthority",
    "CanonicalCleanupClaim",
    "cleanup_claim_token",
]
