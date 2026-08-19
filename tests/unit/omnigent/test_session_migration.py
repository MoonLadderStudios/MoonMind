"""MoonLadderStudios/MoonMind#3712 idempotent session migration tests."""

from __future__ import annotations

import pytest

from moonmind.omnigent.session_migration import (
    execute_migration,
    parse_migration_mode,
    plan_migration,
)
from moonmind.omnigent.session_migration_inventory import RecordInventoryView


class FakeCanonicalRepo:
    """In-memory canonical migration repository for hermetic tests."""

    def __init__(self, *, fail_on: str | None = None) -> None:
        self.applied: set[str] = set()
        self.canonical_sessions: set[str] = set()
        self.aliases: set[str] = set()
        self.quarantined: set[str] = set()
        self.cleanup: set[str] = set()
        self._fail_on = fail_on
        self.create_calls: list[str] = []

    async def is_applied(self, idempotency_key: str) -> bool:
        return idempotency_key in self.applied

    async def record_applied(self, idempotency_key: str) -> None:
        self.applied.add(idempotency_key)

    async def create_canonical_session(self, record_id: str) -> None:
        self.create_calls.append(record_id)
        if record_id == self._fail_on:
            raise RuntimeError("injected canonical create failure")
        self.canonical_sessions.add(record_id)

    async def create_chat_alias(self, record_id: str) -> None:
        self.aliases.add(record_id)

    async def quarantine(self, record_id: str) -> None:
        self.quarantined.add(record_id)

    async def mark_cleanup_required(self, record_id: str) -> None:
        self.cleanup.add(record_id)

    async def has_canonical_session(self, record_id: str) -> bool:
        return record_id in self.canonical_sessions

    async def has_chat_alias(self, record_id: str) -> bool:
        return record_id in self.aliases

    async def is_quarantined(self, record_id: str) -> bool:
        return record_id in self.quarantined


def _view(record_id: str, **overrides: object) -> RecordInventoryView:
    base: dict[str, object] = {
        "record_id": record_id,
        "immutable_evidence_complete": True,
    }
    base.update(overrides)
    return RecordInventoryView(**base)


def _representative_views() -> list[RecordInventoryView]:
    return [
        _view("n1", has_canonical_session=True),
        _view("a1", is_active=True),
        _view("c1", is_terminal=True, authority_provable=True),
        _view("c2", is_terminal=True, authority_provable=True, requires_chat_alias=True),
        _view("t1", is_terminal=True),
        _view("al1", is_terminal=True, requires_chat_alias=True),
        _view("q1", is_terminal=True, conflicting_authority=True),
        _view("clean1", is_terminal=True, cleanup_pending=True),
    ]


def test_parse_migration_mode_fails_closed() -> None:
    assert parse_migration_mode("dry-run") == "dry_run"
    assert parse_migration_mode("rollback-metadata") == "rollback_metadata"
    with pytest.raises(ValueError):
        parse_migration_mode("delete_everything")


def test_clean_database_plans_nothing() -> None:
    plan = plan_migration([])
    assert plan.actions == ()
    assert plan.summary() == {}


@pytest.mark.asyncio
async def test_dry_run_performs_no_writes() -> None:
    plan = plan_migration(_representative_views())
    repo = FakeCanonicalRepo()
    result = await execute_migration(plan, repo, mode="dry_run")
    assert result.mode == "dry_run"
    assert result.created_canonical_sessions == 2  # c1, c2
    assert result.created_aliases == 2  # c2 (alias needed) + al1
    assert result.quarantined == 1  # q1
    # Nothing persisted.
    assert repo.canonical_sessions == set()
    assert repo.applied == set()


@pytest.mark.asyncio
async def test_apply_creates_expected_records_and_is_idempotent() -> None:
    plan = plan_migration(_representative_views())
    repo = FakeCanonicalRepo()

    first = await execute_migration(plan, repo, mode="apply")
    assert first.created_canonical_sessions == 2
    assert repo.canonical_sessions == {"c1", "c2"}
    assert repo.aliases == {"c2", "al1"}
    assert repo.quarantined == {"q1"}
    assert repo.cleanup == {"clean1"}

    # Re-running apply must not duplicate any side effect.
    second = await execute_migration(plan, repo, mode="apply")
    assert second.applied == 0
    assert second.skipped_already_applied > 0
    assert repo.canonical_sessions == {"c1", "c2"}
    assert repo.create_calls == ["c1", "c2"]  # no second create call


@pytest.mark.asyncio
async def test_partial_failure_then_resume_without_duplication() -> None:
    plan = plan_migration(_representative_views())
    repo = FakeCanonicalRepo(fail_on="c2")

    with pytest.raises(RuntimeError):
        await execute_migration(plan, repo, mode="apply")
    # c1 committed before the c2 failure; c2 not marked applied.
    assert "c1" in repo.canonical_sessions
    assert "c2" not in repo.canonical_sessions

    # Resume on a healthy repo state: clear the fault and continue.
    repo._fail_on = None
    resumed = await execute_migration(plan, repo, mode="resume")
    assert "c2" in repo.canonical_sessions
    # c1 was already applied and is not created again.
    assert repo.create_calls.count("c1") == 1


@pytest.mark.asyncio
async def test_verify_reports_no_discrepancies_after_apply() -> None:
    plan = plan_migration(_representative_views())
    repo = FakeCanonicalRepo()
    await execute_migration(plan, repo, mode="apply")
    verify = await execute_migration(plan, repo, mode="verify")
    assert verify.discrepancies == ()


@pytest.mark.asyncio
async def test_verify_reports_discrepancies_before_apply() -> None:
    plan = plan_migration(_representative_views())
    repo = FakeCanonicalRepo()
    verify = await execute_migration(plan, repo, mode="verify")
    assert any(d.startswith("missing_canonical_session") for d in verify.discrepancies)
    assert any(d.startswith("missing_chat_alias") for d in verify.discrepancies)
    assert any(d.startswith("missing_quarantine") for d in verify.discrepancies)


@pytest.mark.asyncio
async def test_rollback_metadata_describes_reversal_without_deleting() -> None:
    plan = plan_migration(_representative_views())
    repo = FakeCanonicalRepo()
    await execute_migration(plan, repo, mode="apply")
    meta = await execute_migration(plan, repo, mode="rollback_metadata")
    assert meta.evidence_preserved is True
    # One manifest entry per created canonical session / alias / quarantine.
    reversible = {e.record_id for e in meta.rollback_manifest}
    assert reversible == {"c1", "c2", "al1", "q1"}
    # Rollback metadata never deletes anything.
    assert repo.canonical_sessions == {"c1", "c2"}
    assert repo.quarantined == {"q1"}


@pytest.mark.asyncio
async def test_ambiguous_group_quarantined_not_resolved_by_recency() -> None:
    views = [
        _view(f"dup-{i}", is_terminal=True, duplicate_group=True, conflicting_authority=True)
        for i in range(7)
    ]
    plan = plan_migration(views)
    repo = FakeCanonicalRepo()
    result = await execute_migration(plan, repo, mode="apply")
    assert result.quarantined == 7
    # No canonical session is chosen for any member of the ambiguous group.
    assert repo.canonical_sessions == set()


@pytest.mark.asyncio
async def test_legacy_active_and_terminal_readable_are_retained_no_side_effects() -> None:
    views = [_view("a1", is_active=True), _view("t1", is_terminal=True)]
    plan = plan_migration(views)
    repo = FakeCanonicalRepo()
    result = await execute_migration(plan, repo, mode="apply")
    assert result.applied == 0
    assert repo.canonical_sessions == set()
    assert repo.aliases == set()
    assert repo.quarantined == set()
