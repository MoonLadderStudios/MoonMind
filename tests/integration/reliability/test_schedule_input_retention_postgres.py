"""Real PostgreSQL schedule admission and garbage collection share row locks."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.db import models
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.config.settings import settings
from moonmind.workflows.temporal.artifacts import TemporalArtifactStateError
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import _service
from tests.unit.workflows.temporal.test_schedule_input_retention import (
    FUTURE,
    PAST,
    expired_input,
    save_schedule,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@asynccontextmanager
async def context(tmp_path):
    tables = {
        model.__table__
        for model in (
            models.TemporalArtifact,
            models.TemporalArtifactLink,
            models.TemporalArtifactPin,
            models.TemporalArtifactUseClaim,
            models.TemporalArtifactDeletionIntent,
            models.OmnigentBridgeSession,
            models.TemporalExecutionRemediationLink,
            models.RecurringWorkflowDefinition,
            models.RecurringWorkflowRun,
        )
    }
    while True:
        closure = tables | {
            fk.column.table for table in tables for fk in table.foreign_keys
        }
        if closure == tables:
            break
        tables = closure
    async with isolated_postgres(list(tables)) as sessions:
        yield sessions


def adapter(**overrides):
    return SimpleNamespace(
        create_schedule=AsyncMock(),
        update_schedule=AsyncMock(),
        pause_schedule=AsyncMock(),
        unpause_schedule=AsyncMock(),
        **overrides
    )


def target(artifact_id):
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "inputArtifactRef": artifact_id,
        "initialParameters": {"targetRuntime": "codex_cli"},
    }


async def write_schedule(
    session, artifact_id, temporal, tmp_path, *, definition_id=None
):
    service = RecurringWorkflowsService(
        session,
        temporal_client_adapter=temporal,
        artifact_service=_service(session, tmp_path),
    )
    if definition_id is not None:
        definition = await session.get(
            models.RecurringWorkflowDefinition, definition_id
        )
        return await service.update_definition(
            definition,
            target=target(artifact_id),
            actor=SimpleNamespace(id="operator"),
        )
    return await service.create_definition(
        name="Retained schedule",
        description=None,
        enabled=False,
        schedule_type="cron",
        cron="0 * * * *",
        timezone="UTC",
        scope_type="personal",
        scope_ref=None,
        owner_user_id=None,
        target=target(artifact_id),
        policy={},
        actor=SimpleNamespace(id="operator"),
    )


async def test_postgres_exact_reference_and_disabled_schedule_survive_sweep(tmp_path):
    async with context(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        saved, incidental = [await expired_input(service) for _ in range(2)]
        await save_schedule(
            session,
            {
                "omnigentAuthorityArtifactRefs": [
                    "artifact:" + saved.artifact_id,
                    incidental.artifact_id + "suffix",
                ],
                "description": incidental.artifact_id,
            },
        )
        result = await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        assert result.soft_deleted_count == 1
        assert (
            await service.read(artifact_id=saved.artifact_id, principal="operator")
        )[1]
        assert incidental.status is models.TemporalArtifactStatus.DELETED


@pytest.mark.parametrize("replacing", [False, True])
async def test_schedule_write_wins_lock_before_expiry_sweep(
    tmp_path, monkeypatch, replacing
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with context(tmp_path) as sessions:
        async with sessions() as session:
            artifact_id = (await expired_input(_service(session, tmp_path))).artifact_id
            definition_id = None
            if replacing:
                old_id = (await expired_input(_service(session, tmp_path))).artifact_id
                definition_id = (
                    await write_schedule(session, old_id, adapter(), tmp_path)
                ).id
        admitted, release = asyncio.Event(), asyncio.Event()
        temporal = adapter()

        async def pause_before_commit(**kwargs):
            admitted.set()
            await release.wait()

        temporal.create_schedule.side_effect = pause_before_commit
        temporal.update_schedule.side_effect = pause_before_commit
        async with sessions() as writer, sessions() as collector:
            writer_task = asyncio.create_task(
                write_schedule(
                    writer, artifact_id, temporal, tmp_path, definition_id=definition_id
                )
            )
            await asyncio.wait_for(admitted.wait(), timeout=15)
            sweep_task = asyncio.create_task(
                _service(collector, tmp_path).sweep_lifecycle(
                    principal="service:sweeper", now=FUTURE
                )
            )
            await asyncio.sleep(0.1)
            was_blocked = not sweep_task.done()
            release.set()
            await writer_task
            assert was_blocked
            assert (await sweep_task).soft_deleted_count == 0
        async with sessions() as reader:
            assert (
                await _service(reader, tmp_path).read(
                    artifact_id=artifact_id, principal="operator"
                )
            )[1]
            if replacing:
                # Replacement releases the old input after its target commits.
                assert (
                    await _service(reader, tmp_path).sweep_lifecycle(
                        principal="service:sweeper", now=FUTURE
                    )
                ).soft_deleted_count == 1
                old = await reader.get(models.TemporalArtifact, old_id)
                assert old.status is models.TemporalArtifactStatus.DELETED


@pytest.mark.parametrize("replacing", [False, True])
async def test_expiry_sweep_wins_lock_and_schedule_cannot_save_deleted_input(
    tmp_path, monkeypatch, replacing
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with context(tmp_path) as sessions:
        async with sessions() as session:
            artifact_id = (await expired_input(_service(session, tmp_path))).artifact_id
            definition_id = None
            if replacing:
                old_id = (await expired_input(_service(session, tmp_path))).artifact_id
                definition_id = (
                    await write_schedule(session, old_id, adapter(), tmp_path)
                ).id
        locked, release = asyncio.Event(), asyncio.Event()
        temporal = adapter()
        async with sessions() as collector, sessions() as writer:
            held_input = await writer.get(models.TemporalArtifact, artifact_id)
            assert held_input.status is models.TemporalArtifactStatus.COMPLETE
            service = _service(collector, tmp_path)
            real_lock = service._repository.get_artifact_for_update

            async def pause_after_lock(target_id):
                result = await real_lock(target_id)
                locked.set()
                await release.wait()
                return result

            monkeypatch.setattr(
                service._repository, "get_artifact_for_update", pause_after_lock
            )
            sweep_task = asyncio.create_task(
                service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
            )
            await asyncio.wait_for(locked.wait(), timeout=15)
            writer_task = asyncio.create_task(
                write_schedule(
                    writer, artifact_id, temporal, tmp_path, definition_id=definition_id
                )
            )
            await asyncio.sleep(0.1)
            was_blocked = not writer_task.done()
            release.set()
            assert (await sweep_task).soft_deleted_count == 1
            assert was_blocked
            with pytest.raises(
                (TemporalArtifactStateError, ValueError),
                match="input|readable|deleted|retain",
            ):
                await writer_task
            temporal.create_schedule.assert_not_awaited()
            temporal.update_schedule.assert_not_awaited()
        if replacing:
            async with sessions() as reader:
                definition = await reader.get(
                    models.RecurringWorkflowDefinition, definition_id
                )
                assert definition.target["inputArtifactRef"] == old_id
                assert (
                    await _service(reader, tmp_path).read(
                        artifact_id=old_id, principal="operator"
                    )
                )[1]


async def test_postgres_saved_reference_blocks_destructive_and_metadata_deletion(
    tmp_path,
):
    async with context(tmp_path) as sessions, sessions() as session:
        service = _service(
            session,
            tmp_path,
            lifecycle_hard_delete_after_seconds=86400,
            lifecycle_purge_after_seconds=86400,
        )
        artifact = await expired_input(service)
        definition = await save_schedule(
            session,
            {"omnigentAuthorityArtifactRefs": ["artifact://" + artifact.artifact_id]},
        )
        with pytest.raises(TemporalArtifactStateError, match="schedule"):
            await service.soft_delete(
                artifact_id=artifact.artifact_id, principal="operator"
            )
        # Preserve a retained pre-fix tombstone and its surviving bytes.
        artifact.status = models.TemporalArtifactStatus.DELETED
        artifact.deleted_at = PAST
        await session.commit()
        with pytest.raises(TemporalArtifactStateError, match="schedule"):
            await service.hard_delete(
                artifact_id=artifact.artifact_id, principal="operator"
            )
        artifact.hard_deleted_at = PAST
        artifact.tombstoned_at = PAST
        await session.commit()
        assert await service._repository.has_live_storage_reference(
            storage_backend=artifact.storage_backend, storage_key=artifact.storage_key
        )
        assert await service._repository.storage_keys_with_live_reference(
            storage_backend=artifact.storage_backend,
            storage_keys={artifact.storage_key},
        ) == {artifact.storage_key}
        result = await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        assert result.purged_count == 0
        assert result.purge_protected_count == 1
        assert (
            await session.get(models.TemporalArtifact, artifact.artifact_id) is artifact
        )
        await RecurringWorkflowsService(
            session,
            temporal_client_adapter=adapter(delete_schedule=AsyncMock()),
            artifact_service=service,
        ).delete_definition(definition)
        result = await service.drain_lifecycle(
            principal="service:sweeper", time_budget=timedelta(seconds=10), now=FUTURE
        )
        assert result.purged_count == 1
