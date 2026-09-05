"""Durable lease persistence cost is independent of active concurrency.

Source issue: MoonLadderStudios/MoonMind#3878.

Granting one lease used to rewrite the runtime-wide lease snapshot, so at
capacity ``N`` every grant cost ``O(active leases)`` of durable work and
serialized unrelated profiles behind one another. The incremental path records
only the granted row. These tests pin both halves of that contract: the
workflow asks for an incremental write, and the activity honors it without
touching any other run's row.

The incremental operation itself is the ``grant`` single-row action landed by
MoonLadderStudios/MoonMind#3883; ``_persist_lease_grant`` is the one funnel
every grant call site uses to choose between it and the pre-patch snapshot
rewrite.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    PROVIDER_INCREMENTAL_LEASE_PATCH,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _profile_with_leases(count: int) -> ProfileSlotState:
    profile = ProfileSlotState(
        profile_id="opencode-zen-free",
        max_parallel_runs=count,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source="none",
        purpose_aware_capacity=True,
    )
    for index in range(count):
        profile.reserve(
            f"agent-run-{index}", NOW, purpose="execution_omnigent"
        )
    return profile


def _workflow() -> MoonMindProviderProfileManagerWorkflow:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "opencode"
    return wf


@contextlib.contextmanager
def _patched(incremental: bool, activity):
    """Run with the incremental-lease patch on or off, recording activity calls."""

    with patch(
        "temporalio.workflow.patched",
        side_effect=lambda name: (
            incremental if name == PROVIDER_INCREMENTAL_LEASE_PATCH else False
        ),
    ), patch("temporalio.workflow.execute_activity", side_effect=activity):
        yield


@pytest.mark.asyncio
async def test_a_grant_persists_only_the_granted_row() -> None:
    """At capacity N, a grant must not rewrite the other N-1 leases."""

    wf = _workflow()
    profile = _profile_with_leases(8)
    wf._profiles = {profile.profile_id: profile}
    calls: list[dict[str, Any]] = []

    async def fake_activity(_name, payload, **_kwargs):
        calls.append(payload)
        return {"granted": True, "duplicate": False}

    with _patched(True, fake_activity):
        persisted = await wf._persist_lease_grant(
            profile,
            "agent-run-3",
            purpose="execution_omnigent",
            metadata=profile.lease_metadata["agent-run-3"],
        )

    assert persisted is True
    assert len(calls) == 1
    assert calls[0]["action"] == "grant"
    assert [row["lease_id"] for row in calls[0]["leases"]] == ["agent-run-3"]
    assert calls[0]["runtime_id"] == "opencode"


@pytest.mark.asyncio
async def test_the_persisted_row_carries_its_full_lease_authority() -> None:
    """An incremental row must be recoverable on its own, like a snapshot row."""

    wf = _workflow()
    profile = _profile_with_leases(2)
    wf._profiles = {profile.profile_id: profile}
    calls: list[dict[str, Any]] = []

    async def fake_activity(_name, payload, **_kwargs):
        calls.append(payload)
        return {"granted": True, "duplicate": False}

    with _patched(True, fake_activity):
        await wf._persist_lease_grant(
            profile,
            "agent-run-1",
            purpose="execution_omnigent",
            metadata=profile.lease_metadata["agent-run-1"],
        )

    row = calls[0]["leases"][0]
    assert row["profile_id"] == "opencode-zen-free"
    assert row["lease_id"] == "agent-run-1"
    assert row["workflow_id"] == "agent-run-1"
    assert row["owner_id"] == "agent-run-1"
    assert row["purpose"] == "execution_omnigent"
    assert row["lease_state"] == "held"
    assert row["capacity_scope_ref"] == "provider-profile:opencode-zen-free"


@pytest.mark.asyncio
async def test_a_pre_patch_history_still_rewrites_the_snapshot() -> None:
    """Replay safety: a history recorded before the patch keeps its command."""

    wf = _workflow()
    profile = _profile_with_leases(3)
    wf._profiles = {profile.profile_id: profile}
    calls: list[dict[str, Any]] = []

    async def fake_activity(_name, payload, **_kwargs):
        calls.append(payload)
        return {"saved": len(payload["leases"])}

    with _patched(False, fake_activity):
        await wf._persist_lease_grant(profile, "agent-run-1")

    assert calls[0]["action"] == "save"
    assert len(calls[0]["leases"]) == 3


@pytest.mark.asyncio
async def test_a_failed_incremental_write_reports_failure_not_success() -> None:
    """Provider capacity must stay blocked when its grant is not durable."""

    wf = _workflow()
    profile = _profile_with_leases(1)
    wf._profiles = {profile.profile_id: profile}

    async def fake_activity(_name, _payload, **_kwargs):
        raise RuntimeError("artifacts worker unavailable")

    with _patched(True, fake_activity):
        persisted = await wf._persist_lease_grant(profile, "agent-run-0")

    assert persisted is False


def test_the_incremental_patch_id_is_versioned() -> None:
    assert PROVIDER_INCREMENTAL_LEASE_PATCH.endswith("-v1")


# ---------------------------------------------------------------------------
# Activity side
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return []

            def first(self):
                return None

        return _Result()

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.committed = True

    async def flush(self):
        return None


def _patch_session(recorder: _Recorder):
    @contextlib.asynccontextmanager
    async def _ctx():
        yield recorder

    return patch(
        "api_service.db.base.get_async_session_context",
        side_effect=lambda: _ctx(),
    )


def _delete_targets(recorder: _Recorder) -> list[str]:
    """Return the compiled WHERE text of every DELETE the activity issued."""

    return [
        str(statement)
        for statement in recorder.statements
        if str(statement).lstrip().upper().startswith("DELETE")
    ]


async def _run_action(action: str, leases: list[dict[str, Any]]) -> _Recorder:
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    recorder = _Recorder()
    with _patch_session(recorder):
        await TemporalArtifactActivities(None).provider_profile_sync_slot_leases(
            runtime_id="opencode", leases=leases, action=action
        )
    return recorder


@pytest.mark.asyncio
async def test_grant_deletes_nothing_and_writes_one_row() -> None:
    """A grant must not delete the rows of runs that are still executing."""

    recorder = await _run_action(
        "grant",
        [
            {
                "lease_id": "agent-run-3",
                "workflow_id": "agent-run-3",
                "profile_id": "opencode-zen-free",
                "owner_id": "agent-run-3",
            }
        ],
    )

    assert _delete_targets(recorder) == []
    assert len(recorder.added) == 1


@pytest.mark.asyncio
async def test_save_still_replaces_the_whole_runtime_snapshot() -> None:
    """Eviction and verification rely on snapshot semantics to drop stale rows."""

    recorder = await _run_action(
        "save",
        [{"workflow_id": "agent-run-0", "profile_id": "opencode-zen-free"}],
    )

    deletes = _delete_targets(recorder)
    assert len(deletes) == 1
    assert "workflow_id IN" not in deletes[0]
    assert "runtime_id" in deletes[0]


@pytest.mark.asyncio
async def test_a_grant_without_a_lease_id_is_rejected() -> None:
    """An unidentified grant must never be written as a row."""

    recorder = await _run_action("grant", [{"profile_id": "opencode-zen-free"}])

    assert _delete_targets(recorder) == []
    assert recorder.added == []
