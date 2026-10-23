"""Saved schedule pointers retain inputs independently of running consumers."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from api_service.db import models
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactStateError,
)
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import (
    _complete_artifact,
    _service,
    temporal_db,
)

pytestmark = pytest.mark.asyncio
PAST = datetime.now(UTC) - timedelta(days=400)
FUTURE = datetime.now(UTC) + timedelta(days=400)


async def save_schedule(session, target, *, enabled=False):
    definition = models.RecurringWorkflowDefinition(
        id=uuid4(),
        name="Retained saved work",
        enabled=enabled,
        schedule_type=models.RecurringWorkflowScheduleType.CRON,
        cron="0 * * * *",
        timezone="UTC",
        owner_user_id=None,
        scope_type=models.RecurringWorkflowScopeType.PERSONAL,
        target=target,
        policy={},
    )
    session.add(definition)
    await session.commit()
    return definition


async def expired_input(service):
    artifact = await _complete_artifact(
        service,
        principal="operator",
        payload=b'{"instructions":"retained"}',
        content_type="application/json",
    )
    artifact.expires_at = PAST
    await service._repository.commit()
    return artifact


@pytest.mark.parametrize("enabled", [True, False])
async def test_schedule_input_survives_expiry_sweep_and_remains_readable(
    tmp_path, enabled
):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifact = await expired_input(service)
        await save_schedule(
            session, {"inputArtifactRef": artifact.artifact_id}, enabled=enabled
        )
        result = await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        assert result.soft_deleted_count == 0
        assert (
            await service.read(artifact_id=artifact.artifact_id, principal="operator")
        )[1] == b'{"instructions":"retained"}'
        claim = await service.acquire_saved_work_use(
            artifact_id=artifact.artifact_id,
            principal="operator",
            request_id="scheduled-use",
            operation_kind="restore",
            ttl_seconds=60,
        )
        assert claim.artifact_id == artifact.artifact_id
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=artifact.artifact_id, principal="workflow:unrelated"
            )


async def test_retained_target_keeps_root_and_compact_plan_reference_shapes(tmp_path):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        artifacts = [await expired_input(service) for _ in range(6)]
        control = await expired_input(service)
        await save_schedule(
            session,
            {
                "planArtifactRef": artifacts[0].artifact_id,
                "manifestArtifactRef": "artifact:" + artifacts[1].artifact_id,
                "initialParameters": {
                    "resolvedSkillsetRef": artifacts[2].artifact_id,
                    "omnigentExecutionPlan": {
                        "planArtifactRef": "artifact://" + artifacts[3].artifact_id,
                        "taskInputSnapshotRef": artifacts[4].artifact_id,
                    },
                },
                "omnigentAuthorityArtifactRefs": [artifacts[5].artifact_id],
            },
        )
        assert (
            await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        ).soft_deleted_count == 1
        assert control.status is models.TemporalArtifactStatus.DELETED
        for artifact in artifacts:
            assert (
                await service.read(
                    artifact_id=artifact.artifact_id, principal="operator"
                )
            )[1]


async def test_replacement_releases_input_after_its_last_schedule_reference(tmp_path):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = _service(session, tmp_path)
        old, new, incidental = [await expired_input(service) for _ in range(3)]
        definition = await save_schedule(
            session,
            {
                "inputArtifactRef": old.artifact_id,
                "description": incidental.artifact_id,
                "initialParameters": {"instructions": incidental.artifact_id},
            },
        )
        shared = await save_schedule(
            session,
            {"initialParameters": {"planArtifactRef": "artifact://" + old.artifact_id}},
        )
        definition.target = {
            "inputArtifactRef": "artifact:" + new.artifact_id,
            "description": incidental.artifact_id,
            "omnigentAuthorityArtifactRefs": [incidental.artifact_id + "suffix"],
        }
        await session.commit()
        result = await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        assert result.soft_deleted_count == 1
        assert (await service.read(artifact_id=new.artifact_id, principal="operator"))[
            1
        ]
        assert old.status is models.TemporalArtifactStatus.COMPLETE
        assert incidental.status is models.TemporalArtifactStatus.DELETED
        await session.delete(shared)
        await session.commit()
        assert (
            await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        ).soft_deleted_count == 1
        assert old.status is models.TemporalArtifactStatus.DELETED
        await session.delete(definition)
        await session.commit()
        assert (
            await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        ).soft_deleted_count == 1


async def test_saved_schedule_blocks_soft_hard_and_metadata_deletion(tmp_path):
    async with temporal_db(tmp_path) as sessions, sessions() as session:
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
        # Retained metadata can already be marked deleted by a pre-fix sweep.
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
        result = await service.sweep_lifecycle(principal="service:sweeper", now=FUTURE)
        assert result.purged_count == 0
        assert result.purge_protected_count == 1
        assert (
            await session.get(models.TemporalArtifact, artifact.artifact_id) is artifact
        )
        await session.delete(definition)
        await session.commit()
        result = await service.drain_lifecycle(
            principal="service:sweeper", time_budget=timedelta(seconds=10), now=FUTURE
        )
        assert result.purged_count == 1
