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
from datetime import datetime, timedelta, timezone
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


class _LeaseRow:
    """A minimal stand-in for a ProviderProfileSlotLease ORM row."""

    def __init__(self, **fields: Any) -> None:
        self.runtime_id = fields.get("runtime_id", "opencode")
        self.workflow_id = fields.get("workflow_id", "agent-run-3")
        self.profile_id = fields.get("profile_id", "opencode-zen-free")
        self.lease_id = fields.get("lease_id", "agent-run-3")
        self.owner_id = fields.get("owner_id", "agent-run-3")
        self.purpose = fields.get("purpose", "execution_direct")
        self.owner_is_workflow = fields.get("owner_is_workflow", True)
        self.step_execution_id = fields.get("step_execution_id")
        self.oauth_session_id = fields.get("oauth_session_id")
        self.idempotency_key = fields.get("idempotency_key")
        self.execution_plan_ref = fields.get("execution_plan_ref")
        self.credential_generation = fields.get("credential_generation")
        self.lease_state = fields.get("lease_state", "held")
        self.fencing_generation = fields.get("fencing_generation", 1)
        self.safe_metadata_json = fields.get("safe_metadata_json")
        self.expires_at = fields.get("expires_at")
        self.granted_at = fields.get("granted_at")
        self.released_at = fields.get("released_at")


class _LeaseTableRecorder(_Recorder):
    """Serves lease rows from memory for grant/release/load/purge actions."""

    def __init__(self, rows: list[_LeaseRow] | None = None) -> None:
        super().__init__()
        self.table: dict[str, _LeaseRow] = {
            row.lease_id: row for row in (rows or [])
        }
        self.deleted: list[str] = []

    async def execute(self, statement):  # type: ignore[override]
        self.statements.append(statement)
        text = str(statement)
        recorder = self

        class _Scalars:
            def all(self) -> list[_LeaseRow]:
                return list(recorder.table.values())

            def first(self) -> _LeaseRow | None:
                params = statement.compile().params
                lease_id = next(
                    (
                        value
                        for key, value in params.items()
                        if key.startswith("lease_id")
                    ),
                    None,
                )
                if lease_id is not None:
                    return recorder.table.get(str(lease_id))
                rows = list(recorder.table.values())
                return rows[0] if rows else None

        class _Result:
            def scalars(self) -> _Scalars:
                return _Scalars()

            def scalar(self) -> Any:
                if "max(" in text.lower():
                    generations = [
                        row.fencing_generation or 0
                        for row in recorder.table.values()
                    ]
                    return max(generations) if generations else None
                return None

            @property
            def rowcount(self) -> int:
                return len(recorder.deleted)

        upper = text.lstrip().upper()
        if upper.startswith("DELETE"):
            params = statement.compile().params
            horizon = next(
                (
                    value
                    for value in params.values()
                    if isinstance(value, datetime)
                ),
                None,
            )
            for lease_id, row in list(recorder.table.items()):
                if row.lease_state != "released" or row.released_at is None:
                    continue
                released_at = row.released_at
                if released_at.tzinfo is None:
                    released_at = released_at.replace(tzinfo=timezone.utc)
                if horizon is None or released_at < horizon:
                    del recorder.table[lease_id]
                    recorder.deleted.append(lease_id)
        return _Result()

    async def delete(self, row: _LeaseRow) -> None:  # type: ignore[override]
        self.table.pop(row.lease_id, None)
        self.deleted.append(row.lease_id)


async def _run_table_action(
    action: str,
    leases: list[dict[str, Any]],
    rows: list[_LeaseRow] | None = None,
) -> _LeaseTableRecorder:
    from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

    recorder = _LeaseTableRecorder(rows)
    with _patch_session(recorder):
        recorder.result = (
            await TemporalArtifactActivities(None).provider_profile_sync_slot_leases(
                runtime_id="opencode", leases=leases, action=action
            )
        )
    return recorder


def _grant_payload(
    generation: int,
    *,
    purpose: str = "execution_omnigent",
    identity: str = "evidence-1",
    lease_id: str = "agent-run-3",
) -> dict[str, Any]:
    return {
        "lease_id": lease_id,
        "workflow_id": lease_id,
        "profile_id": "opencode-zen-free",
        "owner_id": lease_id,
        "purpose": purpose,
        "fencing_generation": generation,
        "safe_metadata": {"evidenceIdentity": identity},
    }


@pytest.mark.asyncio
async def test_a_duplicate_grant_verifies_the_full_grant_identity() -> None:
    """An idempotent retry of the same grant acks without touching the row."""

    recorder = await _run_table_action(
        "grant",
        [_grant_payload(5)],
        [
            _LeaseRow(
                purpose="execution_omnigent",
                fencing_generation=5,
                safe_metadata_json={"evidenceIdentity": "evidence-1"},
            )
        ],
    )

    assert recorder.result == {"granted": True, "duplicate": True}
    assert recorder.added == []
    assert recorder.deleted == []


@pytest.mark.asyncio
async def test_a_stale_row_is_replaced_by_a_newer_grant() -> None:
    """A newer generation atomically replaces a row a failed release left behind."""

    recorder = await _run_table_action(
        "grant",
        [_grant_payload(6)],
        [
            _LeaseRow(
                fencing_generation=5,
                safe_metadata_json={"evidenceIdentity": "evidence-1"},
            )
        ],
    )

    assert recorder.result == {"granted": True, "duplicate": True, "replaced": True}
    assert recorder.deleted == ["agent-run-3"]
    assert len(recorder.added) == 1
    assert recorder.added[0].fencing_generation == 6


@pytest.mark.asyncio
async def test_an_older_grant_never_downgrades_the_fence() -> None:
    """A persisted row newer than the request fails closed, not replaced."""

    recorder = await _run_table_action(
        "grant",
        [_grant_payload(5)],
        [
            _LeaseRow(
                fencing_generation=6,
                safe_metadata_json={"evidenceIdentity": "evidence-1"},
            )
        ],
    )

    assert recorder.result == {"error": "lease fencing regression"}
    assert recorder.added == []
    assert recorder.table["agent-run-3"].fencing_generation == 6


@pytest.mark.asyncio
async def test_a_release_tombstones_instead_of_deleting() -> None:
    """The released row keeps its generation as high-water evidence."""

    recorder = await _run_table_action(
        "release_one",
        [{"lease_id": "agent-run-3", "fencing_generation": 5}],
        [_LeaseRow(fencing_generation=5)],
    )

    assert recorder.result == {"released": True}
    row = recorder.table["agent-run-3"]
    assert row.lease_state == "released"
    assert row.released_at is not None


@pytest.mark.asyncio
async def test_load_excludes_tombstones_but_reports_the_high_water_mark() -> None:
    """A fresh manager resumes above every issued generation, live or not."""

    recorder = await _run_table_action(
        "load",
        [],
        [
            _LeaseRow(
                lease_id="live",
                fencing_generation=4,
                safe_metadata_json={"evidenceIdentity": "evidence-1"},
            ),
            _LeaseRow(
                lease_id="gone",
                fencing_generation=9,
                lease_state="released",
                released_at=datetime.now(timezone.utc),
            ),
        ],
    )

    assert recorder.result["max_fencing_generation"] == 9
    load_statement = recorder.statements[0]
    assert "lease_state" in str(load_statement)
    assert "held" in list(load_statement.compile().params.values())


@pytest.mark.asyncio
async def test_a_grant_never_acks_a_tombstone_as_a_live_duplicate() -> None:
    """A released row is dead authority: re-granting resurrects the row."""

    recorder = await _run_table_action(
        "grant",
        [_grant_payload(10)],
        [
            _LeaseRow(
                fencing_generation=9,
                lease_state="released",
                released_at=datetime.now(timezone.utc),
                safe_metadata_json={"evidenceIdentity": "evidence-1"},
            )
        ],
    )

    assert recorder.result == {"granted": True, "duplicate": True, "replaced": True}
    assert recorder.deleted == ["agent-run-3"]
    assert len(recorder.added) == 1
    assert recorder.added[0].lease_state == "held"


@pytest.mark.asyncio
async def test_purge_reaps_only_tombstones_past_the_horizon() -> None:
    """Bounded table growth that cannot reap a still-redeliverable release."""

    now = datetime.now(timezone.utc)
    recorder = await _run_table_action(
        "purge_released",
        [{"older_than_seconds": 30 * 24 * 3600}],
        [
            _LeaseRow(
                lease_id="held",
                fencing_generation=7,
            ),
            _LeaseRow(
                lease_id="ancient",
                fencing_generation=3,
                lease_state="released",
                released_at=now - timedelta(days=40),
            ),
            _LeaseRow(
                lease_id="fresh",
                fencing_generation=6,
                lease_state="released",
                released_at=now - timedelta(hours=1),
            ),
        ],
    )

    assert recorder.result == {"purged": 1}
    assert sorted(recorder.table) == ["fresh", "held"]
    purge_text = str(recorder.statements[0])
    assert "lease_state" in purge_text
    assert "released" in list(recorder.statements[0].compile().params.values())
