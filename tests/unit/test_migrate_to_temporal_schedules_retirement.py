"""Retirement guards for the Temporal-schedule migration script.

MoonLadderStudios/MoonMind#4188 (MR1): the native Manifest product — the
legacy ``manifest_run`` target kind and its ``MoonMind.ManifestIngest``
successor — is retired. The one-time migration must never (re)create a
Temporal Schedule for retired work and must never silently convert it to an
ordinary run. Retired definitions are skipped with protected evidence left to
the export/disable path; ``temporal_schedule_id`` stays unset so the
definition cannot reactivate through trigger/backfill/resume.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from scripts.migrate_to_temporal_schedules import (
    _workflow_type_for_target,
    migrate_definitions,
)



def test_manifest_run_kind_has_no_workflow_type() -> None:
    assert _workflow_type_for_target({"kind": "manifest_run"}) is None


def test_explicit_manifest_ingest_type_has_no_workflow_type() -> None:
    assert (
        _workflow_type_for_target({"workflowType": "MoonMind.ManifestIngest"})
        is None
    )
    assert (
        _workflow_type_for_target({"workflow_type": "MoonMind.ManifestIngest"})
        is None
    )


def test_supported_kinds_still_map_to_run() -> None:
    assert _workflow_type_for_target({"kind": "queue_task"}) == "MoonMind.Run"
    assert (
        _workflow_type_for_target({"kind": "queue_task_template"})
        == "MoonMind.Run"
    )
    assert _workflow_type_for_target({}) == "MoonMind.Run"


async def _run_migrate_with_definitions(
    monkeypatch: pytest.MonkeyPatch, definitions: list
) -> MagicMock:
    """Run migrate_definitions against in-memory definitions; return adapter."""
    import scripts.migrate_to_temporal_schedules as migrate_module

    sessions: list = []

    class _FakeResult:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        def scalars(self) -> SimpleNamespace:
            rows = self._rows
            return SimpleNamespace(all=lambda: rows)

    class _FakeSession:
        def __init__(self, rows: list) -> None:
            self._rows = rows
            self.added: list = []
            self.commits = 0

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def execute(self, stmt: object) -> _FakeResult:
            return _FakeResult(self._rows)

        def add(self, row: object) -> None:
            self.added.append(row)

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            pass

    fake_session = _FakeSession(definitions)
    sessions.append(fake_session)

    def _session_maker() -> _FakeSession:
        return fake_session

    monkeypatch.setattr(
        migrate_module, "AppSettings", lambda: SimpleNamespace(database=SimpleNamespace(POSTGRES_URL="x"))
    )
    monkeypatch.setattr(
        migrate_module, "_create_session_maker", lambda *a, **k: _session_maker
    )
    adapter = MagicMock()
    adapter.create_schedule = AsyncMock(return_value="mm-schedule:id")
    monkeypatch.setattr(migrate_module, "TemporalClientAdapter", lambda: adapter)
    await migrate_definitions()
    return adapter


def _definition(*, target: dict, name: str = "Def") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        owner_user_id=uuid4(),
        cron="0 6 * * *",
        timezone="UTC",
        enabled=True,
        policy={},
        target=target,
        temporal_schedule_id=None,
    )


@pytest.mark.asyncio
async def test_migrate_skips_manifest_run_without_creating_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retired = _definition(target={"kind": "manifest_run"}, name="Retired")
    adapter = await _run_migrate_with_definitions(monkeypatch, [retired])
    adapter.create_schedule.assert_not_called()
    assert retired.temporal_schedule_id is None


@pytest.mark.asyncio
async def test_migrate_skips_manifest_ingest_type_without_creating_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retired = _definition(
        target={"workflowType": "MoonMind.ManifestIngest"}, name="Retired"
    )
    adapter = await _run_migrate_with_definitions(monkeypatch, [retired])
    adapter.create_schedule.assert_not_called()
    assert retired.temporal_schedule_id is None


@pytest.mark.asyncio
async def test_migrate_still_migrates_ordinary_definitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinary = _definition(target={"kind": "queue_task"}, name="Ordinary")
    retired = _definition(target={"kind": "manifest_run"}, name="Retired")
    adapter = await _run_migrate_with_definitions(
        monkeypatch, [ordinary, retired]
    )
    adapter.create_schedule.assert_called_once()
    assert ordinary.temporal_schedule_id == f"mm-schedule:{ordinary.id}"
    assert retired.temporal_schedule_id is None
