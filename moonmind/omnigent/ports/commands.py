"""Durable command / idempotency-journal repository port.

Source issue: MoonLadderStudios/MoonMind#3711.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from moonmind.omnigent.control_plane.records import (
    CasResult,
    CommandRecord,
    ControlPlaneOutcome,
)


@runtime_checkable
class CommandRepositoryPort(Protocol):
    """Narrow interface for the durable logical-side-effect command journal.

    Owns command idempotency, exclusive claim authority, and delivery-outcome
    recording (including ambiguous-delivery marking) so a claimed side effect is
    executed by exactly one owner.
    """

    async def record(
        self,
        *,
        command_id: str,
        session_id: str,
        command_type: str,
        idempotency_key: str,
        payload_digest: str,
        turn_attempt_id: Optional[str] = None,
        expected_session_revision: Optional[int] = None,
        fencing_generation: int = 0,
        owner_class: Optional[str] = None,
        retry_policy: Optional[dict[str, Any]] = None,
    ) -> CommandRecord: ...

    async def get(self, command_id: str) -> Optional[CommandRecord]: ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> Optional[CommandRecord]: ...

    async def list_for_session(
        self, session_id: str
    ) -> list[CommandRecord]: ...

    async def active_for_session(
        self, session_id: str
    ) -> Optional[CommandRecord]: ...

    async def claim_command(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
    ) -> CasResult: ...

    async def record_command_delivery(
        self,
        command_id: str,
        *,
        owner_class: str,
        claim_token: str,
        outcome: ControlPlaneOutcome,
        provider_receipt_id: Optional[str] = None,
        result_ref: Optional[str] = None,
    ) -> CasResult: ...


__all__ = ["CommandRepositoryPort"]
