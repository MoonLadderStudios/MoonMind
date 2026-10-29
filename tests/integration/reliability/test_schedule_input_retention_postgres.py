"""Real PostgreSQL schedule admission and garbage collection share row locks."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError

from api_service.db import models
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)
from moonmind.config.settings import settings
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.artifacts import (
    ArtifactTransactionOwnership,
    TemporalArtifactRepository,
    TemporalArtifactStateError,
)
from tests.artifact_access import access_for_test
from tests.support.isolated_postgres import isolated_postgres
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
    _complete_artifact,
    _service,
)
from tests.unit.workflows.temporal.test_schedule_input_retention import (
    FUTURE,
    PAST,
    save_schedule,
)
from tests.unit.workflows.temporal.test_schedule_input_retention import (
    expired_input as _expired_input,
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
            models.TemporalArtifactDependency,
            models.TemporalExecutionCanonicalRecord,
            models.TemporalArtifactLink,
            models.TemporalArtifactPin,
            models.TemporalArtifactUseClaim,
            models.TemporalArtifactDeletionIntent,
            models.OmnigentBridgeSession,
            models.TemporalExecutionRemediationLink,
            models.RecurringWorkflowDefinition,
            models.RecurringWorkflowRun,
            models.AgentSkillDefinition,
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


async def expired_input(service):
    # Valid at admission time, expired at the collector's explicit FUTURE clock.
    artifact = await _expired_input(service)
    artifact.expires_at = datetime.now(UTC) + timedelta(days=1)
    await service._repository.commit()
    return artifact


def adapter(**overrides):
    return SimpleNamespace(
        create_schedule=AsyncMock(),
        update_schedule=AsyncMock(),
        pause_schedule=AsyncMock(),
        unpause_schedule=AsyncMock(),
        **overrides,
    )


def target(artifact_id):
    return {
        "workflowType": "MoonMind.UserWorkflow",
        "inputArtifactRef": artifact_id,
        "initialParameters": {"targetRuntime": "codex_cli"},
    }


def observe_writer_claim(monkeypatch, artifact_id, acquired):
    # CALLER reads acquire a use-claim FK lock before producer sealing locks.
    # Observe that real first lock, not only the later FOR UPDATE facade.
    original = TemporalArtifactRepository.acquire_use_claim

    async def observe(repository, **kwargs):
        result = await original(repository, **kwargs)
        if (
            repository._session.info.get("retention_race_role") == "writer"
            and kwargs["artifact_id"] == artifact_id
        ):
            acquired.set()
        return result

    monkeypatch.setattr(TemporalArtifactRepository, "acquire_use_claim", observe)


async def write_schedule(
    session, artifact_id, temporal, tmp_path, *, definition_id=None, plan_id=None
):
    payload = target(artifact_id)
    if plan_id is not None:
        payload["planArtifactRef"] = plan_id
    service = RecurringWorkflowsService(
        session,
        temporal_client_adapter=temporal,
        artifact_service=_service(
            session, tmp_path, transaction_ownership=ArtifactTransactionOwnership.CALLER
        ),
    )
    if definition_id is not None:
        definition = await session.get(
            models.RecurringWorkflowDefinition, definition_id
        )
        return await service.update_definition(
            definition,
            target=payload,
            actor=OperatorAdmission(via="loopback"),
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
        target=payload,
        policy={},
        actor=OperatorAdmission(via="loopback"),
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
            await service.read(
                artifact_id=saved.artifact_id, access=access_for_test("operator")
            )
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

        retain = RecurringWorkflowsService._retain_target_artifacts

        async def pause_before_commit(service, target, **kwargs):
            root_id = await retain(service, target, **kwargs)
            admitted.set()
            await release.wait()
            return root_id

        monkeypatch.setattr(
            RecurringWorkflowsService, "_retain_target_artifacts", pause_before_commit
        )
        rpc_observations = []

        async def inspect_committed_inputs(**kwargs):
            # The RPC no longer contains uncommitted admission. A separate
            # consumer sees the definition and its retained input beforehand.
            async with sessions() as observer:
                definition = await observer.get(
                    models.RecurringWorkflowDefinition, kwargs["definition_id"]
                )
                assert definition is not None
                assert definition.target["inputArtifactRef"] == artifact_id
                service = _service(observer, tmp_path)
                assert (
                    await service.read(
                        artifact_id=artifact_id, access=access_for_test("operator")
                    )
                )[1]
                result = await service.sweep_lifecycle(
                    principal="service:sweeper", now=FUTURE
                )
                # Replacing a definition releases its old input. Only that
                # retired input may be collected, never the newly admitted one.
                assert result.soft_deleted_count == int(replacing)
                assert (
                    await service.read(
                        artifact_id=artifact_id, access=access_for_test("operator")
                    )
                )[1]
                rpc_observations.append(True)

        temporal.create_schedule.side_effect = inspect_committed_inputs
        temporal.update_schedule.side_effect = inspect_committed_inputs
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
            assert rpc_observations == [True]
        async with sessions() as reader:
            assert (
                await _service(reader, tmp_path).read(
                    artifact_id=artifact_id, access=access_for_test("operator")
                )
            )[1]
            if replacing:
                # Replacement retires both the old producer root and its input.
                # The next bounded page collects the remaining retired row.
                assert (
                    await _service(reader, tmp_path).sweep_lifecycle(
                        principal="service:sweeper", now=FUTURE
                    )
                ).soft_deleted_count == 1
                assert (
                    await _service(reader, tmp_path).sweep_lifecycle(
                        principal="service:sweeper", now=FUTURE
                    )
                ).soft_deleted_count == 0
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

            async def pause_after_lock(target_id, **kwargs):
                result = await real_lock(target_id, **kwargs)
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
                        artifact_id=old_id, access=access_for_test("operator")
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


async def test_multi_input_schedule_and_expiry_page_do_not_deadlock(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with context(tmp_path) as sessions:
        async with sessions() as session:
            service = _service(session, tmp_path)
            artifacts = [await expired_input(service) for _ in range(2)]
            first_id, second_id = sorted(item.artifact_id for item in artifacts)
            for artifact in artifacts:
                artifact.expires_at = (FUTURE - timedelta(days=1)) + (
                    timedelta(hours=1)
                    if artifact.artifact_id == first_id
                    else timedelta()
                )
            await session.commit()
        collector_has_second, writer_has_first = asyncio.Event(), asyncio.Event()
        original_lock = TemporalArtifactRepository.get_artifact_for_update
        observe_writer_claim(monkeypatch, first_id, writer_has_first)

        async def interleaved_lock(repository, artifact_id, **kwargs):
            artifact = await original_lock(repository, artifact_id, **kwargs)
            role = repository._session.info.get("retention_race_role")
            if role == "collector" and artifact_id == second_id:
                collector_has_second.set()
                await writer_has_first.wait()
            elif role == "writer" and artifact_id == first_id:
                writer_has_first.set()
            return artifact

        monkeypatch.setattr(
            TemporalArtifactRepository, "get_artifact_for_update", interleaved_lock
        )

        async def collect():
            async with sessions() as session:
                session.info["retention_race_role"] = "collector"
                return await _service(session, tmp_path).sweep_lifecycle(
                    principal="service:sweeper", now=FUTURE
                )

        async def write():
            async with sessions() as session:
                session.info["retention_race_role"] = "writer"
                return await write_schedule(
                    session, first_id, adapter(), tmp_path, plan_id=second_id
                )

        collector = asyncio.create_task(collect())
        await asyncio.wait_for(collector_has_second.wait(), timeout=15)
        writer = asyncio.create_task(write())
        collected, written = await asyncio.wait_for(
            asyncio.gather(collector, writer, return_exceptions=True), timeout=20
        )
        assert not isinstance(collected, DBAPIError), str(collected)
        assert not isinstance(written, DBAPIError), str(written)
        assert collected.soft_deleted_count == 2
        assert isinstance(written, RecurringWorkflowValidationError)


async def shared_input_pair(service, tag):
    payload = ('{"instructions":"' + tag + '"}').encode()
    pair = [
        await _complete_artifact(
            service,
            principal="operator",
            payload=payload,
            content_type="application/json",
            content_addressed_scope="retention-race",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        for _ in range(2)
    ]
    live, deleted = sorted(pair, key=lambda item: item.artifact_id)
    live.expires_at = None
    await service.soft_delete(artifact_id=deleted.artifact_id, principal="operator")
    deleted.deleted_at = PAST
    await service._repository.commit()
    return live, deleted


async def test_shared_blob_hard_sweep_and_multi_input_schedule_do_not_deadlock(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with context(tmp_path) as sessions:
        async with sessions() as session:
            service = _service(session, tmp_path)
            pairs = [await shared_input_pair(service, tag) for tag in ("a", "b")]
            (first, first_deleted), (second, _second_deleted) = sorted(
                pairs, key=lambda pair: pair[0].artifact_id
            )
            first_deleted.deleted_at = PAST + timedelta(hours=1)
            await session.commit()
            first_id, second_id = first.artifact_id, second.artifact_id
            second_key = second.storage_key
        collector_has_second, writer_has_first = asyncio.Event(), asyncio.Event()
        original_group = TemporalArtifactRepository.lock_storage_references
        original_lock = TemporalArtifactRepository.get_artifact_for_update
        observe_writer_claim(monkeypatch, first_id, writer_has_first)

        async def interleaved_group(repository, **kwargs):
            await original_group(repository, **kwargs)
            if (
                repository._session.info.get("retention_race_role") == "collector"
                and kwargs["storage_key"] == second_key
            ):
                collector_has_second.set()
                await writer_has_first.wait()

        async def observe_writer_lock(repository, artifact_id, **kwargs):
            artifact = await original_lock(repository, artifact_id, **kwargs)
            if (
                repository._session.info.get("retention_race_role") == "writer"
                and artifact_id == first_id
            ):
                writer_has_first.set()
            return artifact

        monkeypatch.setattr(
            TemporalArtifactRepository, "lock_storage_references", interleaved_group
        )
        monkeypatch.setattr(
            TemporalArtifactRepository, "get_artifact_for_update", observe_writer_lock
        )

        async def collect():
            async with sessions() as session:
                session.info["retention_race_role"] = "collector"
                return await _service(session, tmp_path).sweep_lifecycle(
                    principal="service:sweeper", now=FUTURE
                )

        async def write():
            async with sessions() as session:
                session.info["retention_race_role"] = "writer"
                return await write_schedule(
                    session, first_id, adapter(), tmp_path, plan_id=second_id
                )

        collector = asyncio.create_task(collect())
        await asyncio.wait_for(collector_has_second.wait(), timeout=15)
        writer = asyncio.create_task(write())
        collected, written = await asyncio.wait_for(
            asyncio.gather(collector, writer, return_exceptions=True), timeout=20
        )
        assert not isinstance(collected, DBAPIError), str(collected)
        assert not isinstance(written, DBAPIError), str(written)
        assert collected.hard_deleted_count == 2
        assert written.target["inputArtifactRef"] == first_id
        async with sessions() as session:
            reader = _service(session, tmp_path)
            for artifact_id in (first_id, second_id):
                assert (
                    await reader.read(
                        artifact_id=artifact_id, access=access_for_test("operator")
                    )
                )[1]


async def test_explicit_shared_delete_and_schedule_admission_do_not_deadlock(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    async with context(tmp_path) as sessions:
        async with sessions() as session:
            live, deleted = await shared_input_pair(_service(session, tmp_path), "held")
            first_id, deleted_id = live.artifact_id, deleted.artifact_id
        deleter_ready, writer_has_first = asyncio.Event(), asyncio.Event()
        group_requested = asyncio.Event()
        original_group = TemporalArtifactRepository.lock_storage_references
        original_lock = TemporalArtifactRepository.get_artifact_for_update
        observe_writer_claim(monkeypatch, first_id, writer_has_first)

        async def interleaved_group(repository, **kwargs):
            if repository._session.info.get("retention_race_role") == "deleter":
                group_requested.set()
                deleter_ready.set()
                await writer_has_first.wait()
            await original_group(repository, **kwargs)

        async def interleaved_lock(repository, artifact_id, **kwargs):
            artifact = await original_lock(repository, artifact_id, **kwargs)
            role = repository._session.info.get("retention_race_role")
            if role == "deleter" and artifact_id == deleted_id:
                deleter_ready.set()
                if not group_requested.is_set():
                    await writer_has_first.wait()
            elif role == "writer" and artifact_id == first_id:
                writer_has_first.set()
            return artifact

        monkeypatch.setattr(
            TemporalArtifactRepository, "lock_storage_references", interleaved_group
        )
        monkeypatch.setattr(
            TemporalArtifactRepository, "get_artifact_for_update", interleaved_lock
        )

        async def remove():
            async with sessions() as session:
                session.info["retention_race_role"] = "deleter"
                return await _service(session, tmp_path).hard_delete(
                    artifact_id=deleted_id, principal="operator"
                )

        async def write():
            async with sessions() as session:
                session.info["retention_race_role"] = "writer"
                return await write_schedule(
                    session, first_id, adapter(), tmp_path, plan_id=deleted_id
                )

        deleter = asyncio.create_task(remove())
        await asyncio.wait_for(deleter_ready.wait(), timeout=15)
        writer = asyncio.create_task(write())
        removed, written = await asyncio.wait_for(
            asyncio.gather(deleter, writer, return_exceptions=True), timeout=20
        )
        assert not isinstance(removed, DBAPIError), str(removed)
        assert not isinstance(written, DBAPIError), str(written)
        assert removed.hard_deleted_at is not None
        assert isinstance(written, RecurringWorkflowValidationError)
        async with sessions() as session:
            assert (
                await _service(session, tmp_path).read(
                    artifact_id=first_id, access=access_for_test("operator")
                )
            )[1]
