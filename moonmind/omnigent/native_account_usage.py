"""Publish native status-line quota from an Omnigent session stream (#5365).

The claude-native status-line capture forwards ``rate_limits`` on
``session.usage``. This module binds that quota to the step's held Provider
Profile ledger lease and hands it to ``ProviderAccountUsageService`` as a
partial native-owner observation. The lease is looked up in the database from
the admitted profile authorization, never taken from the event. Publishing
never requests collection, reads credentials, or changes the run, and it is
bounded so a slow database cannot hold up the event stream.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from moonmind.provider_profiles.claude_account_usage import (
    ClaudeUsageCollector,
    status_line_report,
)
from moonmind.provider_profiles.usage_collection import (
    bind_native_owner_event,
    default_usage_collectors,
)

logger = logging.getLogger(__name__)

PUBLISH_TIMEOUT_SECONDS = 5.0
_LEASE_REF_PREFIX = "provider-profile-lease:"


class NativeAccountUsagePublisher:
    """Publishes one Omnigent session's native quota for its bound profile."""

    def __init__(
        self,
        *,
        profile_authorization: Mapping[str, Any],
        session_id: str,
        session_factory: Callable[[], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._profile_id = str(profile_authorization.get("providerProfileId") or "")
        lease_ref = str(profile_authorization.get("providerLeaseRef") or "")
        self._lease_id = (
            lease_ref.removeprefix(_LEASE_REF_PREFIX)
            if lease_ref.startswith(_LEASE_REF_PREFIX)
            else ""
        )
        generation = profile_authorization.get("credentialGeneration")
        self._generation = (
            generation
            if isinstance(generation, int) and not isinstance(generation, bool)
            else None
        )
        self._session_id = session_id
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._published: Any = None

    async def observe(self, event: Mapping[str, Any]) -> str | None:
        """Publish quota carried by ``event``; return a bounded outcome label.

        Events without quota return ``None``. Nothing here raises: a failure
        only loses this reading, which the next change or an idle read
        replaces.
        """

        rate_limits = event.get("rate_limits")
        if event.get("type") != "session.usage" or not isinstance(rate_limits, Mapping):
            return None
        if event.get("conversation_id") != self._session_id:
            # A child session's pane is not this profile's status line.
            return "ignored"
        if not (self._profile_id and self._lease_id and self._generation):
            return "unbound"
        if rate_limits == self._published:
            return "duplicate"
        report = status_line_report(rate_limits)
        if report is None:
            return "unreadable"
        try:
            outcome = await asyncio.wait_for(
                self._publish(report), timeout=PUBLISH_TIMEOUT_SECONDS
            )
        except Exception as exc:
            logger.warning(
                "Native account usage for profile %s was not published: %s",
                self._profile_id,
                type(exc).__name__,
            )
            return "failed"
        if outcome == "accepted":
            self._published = dict(rate_limits)
        return outcome

    def _sessions(self) -> Callable[[], Any]:
        if self._session_factory is not None:
            return self._session_factory
        from api_service.db.base import async_session_maker

        return async_session_maker

    async def _publish(self, report: Any) -> str:
        from sqlalchemy import select

        from api_service.db.models import (
            ManagedAgentProviderProfile,
            ProviderProfileSlotLease,
        )
        from api_service.services.provider_account_usage import (
            ProviderAccountUsageService,
        )

        async with self._sessions()() as session:
            lease = (
                await session.execute(
                    select(ProviderProfileSlotLease).where(
                        ProviderProfileSlotLease.lease_id == self._lease_id,
                        ProviderProfileSlotLease.profile_id == self._profile_id,
                    )
                )
            ).scalar_one_or_none()
            expires_at = getattr(lease, "expires_at", None)
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if (
                lease is None
                or lease.lease_state != "held"
                or lease.released_at is not None
                or expires_at is None
                or expires_at <= self._clock()
            ):
                return "owner_expired"
            if (
                lease.credential_generation is not None
                and lease.credential_generation != self._generation
            ):
                return "stale_generation"
            profile = await session.get(ManagedAgentProviderProfile, self._profile_id)
            if profile is None:
                return "profile_missing"
            adapter = default_usage_collectors().get(
                profile.runtime_id, profile.provider_id
            )
            if not isinstance(adapter, ClaudeUsageCollector):
                return "not_supported"
            observation = bind_native_owner_event(
                owner_lease={
                    "profileId": lease.profile_id,
                    "credentialGeneration": self._generation,
                    "expiresAt": expires_at,
                },
                provider_id=profile.provider_id,
                runtime_id=profile.runtime_id,
                report=report,
            )
            result = await ProviderAccountUsageService(
                session, clock=self._clock
            ).ingest_observation(observation)
        if result.accepted:
            return "accepted"
        return result.rejection.value if result.rejection else "rejected"


__all__ = ["PUBLISH_TIMEOUT_SECONDS", "NativeAccountUsagePublisher"]
