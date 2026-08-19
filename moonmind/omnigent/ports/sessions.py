"""Canonical provider-session repository port.

Source issue: MoonLadderStudios/MoonMind#3711.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from moonmind.omnigent.control_plane.records import (
    CasResult,
    FencingScope,
    SessionRecord,
)

_UNSET = object()


@runtime_checkable
class SessionRepositoryPort(Protocol):
    """Narrow interface for the canonical provider-session authority aggregate.

    Owns the single canonical session record, its revision/fencing authority,
    and the fenced lifecycle writes that converge desired, observed, and
    reconciled state. Excludes turn, observation, command, and decision
    persistence -- those live behind their own ports.
    """

    async def create(
        self,
        *,
        session_id: str,
        moonmind_workflow_id: str,
        provider: str,
        moonmind_run_id: Optional[str] = None,
        step_execution_id: Optional[str] = None,
        moonmind_agent_run_id: Optional[str] = None,
        compatibility_profile: Optional[str] = None,
        provider_session_ref: Optional[str] = None,
        chat_binding_id: Optional[str] = None,
        intent_ref: Optional[str] = None,
        intent_digest: Optional[str] = None,
        desired_state: str = "pending",
        provider_profile_id: Optional[str] = None,
        host_binding_ref: Optional[str] = None,
        host_lease_ref: Optional[str] = None,
        compatibility_ref: Optional[str] = None,
        image_manifest_ref: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SessionRecord: ...

    async def get(self, session_id: str) -> Optional[SessionRecord]: ...

    async def load_for_update(
        self, session_id: str
    ) -> Optional[SessionRecord]: ...

    async def get_by_scope(
        self, moonmind_workflow_id: str, provider_session_ref: str
    ) -> Optional[SessionRecord]: ...

    async def get_by_chat_binding(
        self, chat_binding_id: str
    ) -> Optional[SessionRecord]: ...

    async def compare_and_swap_session(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: Optional[int] = None,
        expected_host_lease_generation: Optional[int] = None,
        desired_state: Any = _UNSET,
        observed_state: Any = _UNSET,
        reconciled_state: Any = _UNSET,
        active_turn_attempt_id: Any = _UNSET,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
        cleanup_state: Any = _UNSET,
        historical_read_state: Any = _UNSET,
        next_reconciliation_deadline: Any = _UNSET,
        last_decision_ref: Any = _UNSET,
    ) -> CasResult: ...

    async def update_lifecycle(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        expected_provider_profile_generation: Optional[int] = None,
        expected_host_lease_generation: Optional[int] = None,
        desired_state: Any = _UNSET,
        observed_state: Any = _UNSET,
        reconciled_state: Any = _UNSET,
        active_turn_attempt_id: Any = _UNSET,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
        cleanup_state: Any = _UNSET,
        historical_read_state: Any = _UNSET,
        next_reconciliation_deadline: Any = _UNSET,
        last_decision_ref: Any = _UNSET,
    ) -> SessionRecord: ...

    async def acquire_fencing_generation(
        self,
        session_id: str,
        scope: FencingScope,
        *,
        expected_revision: int,
    ) -> SessionRecord: ...

    async def advance_observation_frontier(
        self,
        session_id: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        provider_event_cursor: Any = _UNSET,
        snapshot_frontier: Any = _UNSET,
    ) -> CasResult: ...

    async def mark_terminal(
        self,
        session_id: str,
        terminal_state: str,
        *,
        expected_revision: int,
        expected_fencing_generation: int,
        terminal_evidence_ref: Optional[str] = None,
    ) -> SessionRecord: ...


__all__ = ["SessionRepositoryPort"]
