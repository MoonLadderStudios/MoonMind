"""Shared builders for the pure Omnigent lifecycle reconciler tests.

Source issue: MoonLadderStudios/MoonMind#3702.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from moonmind.omnigent.reconciler import (
    CompiledSessionIntent,
    DurableSessionState,
    LeaseState,
    ObservationSet,
    ReconciliationDecision,
    SubmissionState,
    reconcile,
)

FIXED_NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def now() -> datetime:
    return FIXED_NOW


@pytest.fixture
def make_intent():
    def _make(**overrides: Any) -> CompiledSessionIntent:
        base: dict[str, Any] = {
            "session_id": "session-1",
            "provider": "omnigent",
            "turn_prompt_digest": "sha256:prompt",
        }
        base.update(overrides)
        return CompiledSessionIntent(**base)

    return _make


@pytest.fixture
def make_durable():
    def _make(**overrides: Any) -> DurableSessionState:
        base: dict[str, Any] = {
            "session_id": "session-1",
            "revision": 7,
            "owner_token": "owner-token",
            "fencing_generation": 3,
        }
        base.update(overrides)
        return DurableSessionState(**base)

    return _make


@pytest.fixture
def make_ready_durable(make_durable):
    """A durable state that has provisioned leases/session and accepted a turn."""

    def _make(**overrides: Any) -> DurableSessionState:
        base: dict[str, Any] = {
            "profile_lease": LeaseState.HELD,
            "host_lease": LeaseState.HELD,
            "provider_session_attached": True,
            "provider_session_id": "provider-session-1",
            "attempt_id": "attempt-1",
            "submission": SubmissionState.ACCEPTED,
            "turn_attempts": 1,
        }
        base.update(overrides)
        return make_durable(**base)

    return _make


@pytest.fixture
def make_obs():
    def _make(**overrides: Any) -> ObservationSet:
        return ObservationSet(**overrides)

    return _make


@pytest.fixture
def run(now):
    def _run(
        intent: CompiledSessionIntent,
        durable: DurableSessionState,
        observations: ObservationSet | None = None,
        at: datetime | None = None,
    ) -> ReconciliationDecision:
        return reconcile(
            intent=intent,
            durable=durable,
            observations=observations if observations is not None else ObservationSet(),
            now=at if at is not None else now,
        )

    return _run
