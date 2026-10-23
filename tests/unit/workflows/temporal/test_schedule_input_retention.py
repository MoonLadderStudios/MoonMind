"""Saved schedule pointers retain inputs independently of running consumers."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
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


async def test_current_saved_deployment_skill_content_survives_expiry(tmp_path):
    from api_service.services.agent_skills_service import AgentSkillsService

    async with temporal_db(tmp_path) as sessions, sessions() as session:
        artifacts = _service(session, tmp_path)
        skills = AgentSkillsService(session, artifact_service=artifacts)
        await skills.create_skill(slug="current-saved-skill", title="Current Skill")
        skill = await skills.update_skill_content(
            skill_slug="current-saved-skill",
            content="# Current Skill\nKeep this saved content.\n",
            principal="operator",
        )

        await artifacts.sweep_lifecycle(principal="service:sweeper", now=FUTURE)

        assert (
            await artifacts.read(artifact_id=skill.artifact_ref, principal="operator")
        )[1] == b"# Current Skill\nKeep this saved content.\n"


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("legacy_incomplete", [True, False])
async def test_saved_deployment_skill_keeps_its_frozen_content_after_skill_update(
    tmp_path, monkeypatch, enabled, legacy_incomplete
):
    from api_service.services import omnigent_execution_plan_service as plans
    from api_service.services.agent_skills_service import AgentSkillsService
    from api_service.services.recurring_workflows_service import (
        RecurringWorkflowsService,
    )
    from moonmind.omnigent.harness_platform.catalog import (
        TrustState,
        classify_harness_trust,
        create_catalog_snapshot,
    )
    from moonmind.omnigent.harness_platform.catalog_service import (
        DbHarnessCatalogRepository,
        HarnessCatalogSyncResult,
    )
    from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
    from tests.unit.services.test_omnigent_execution_plan_service import (
        _compile_opencode_plan,
        _configure_ready_host_image_pair,
        _protected_support_evidence,
    )

    provenance = _configure_ready_host_image_pair(monkeypatch)
    provenance["hostVersion"] = "0.11.0"
    monkeypatch.setattr(
        plans,
        "resolve_execution_evidence",
        lambda payload, **_kwargs: (_protected_support_evidence(payload), "supported"),
    )
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        catalog = create_catalog_snapshot(
            endpointRef="default",
            omnigentVersion="0.11.0",
            omnigentBuildDigest="sha256:" + "b" * 64,
            sourceDigest="sha256:" + "d" * 64,
            observedAt=datetime.now(UTC),
            harnesses=[
                {
                    "id": "opencode-native",
                    "aliases": [],
                    "label": "OpenCode",
                    "implementation": {
                        "sourceKind": "core",
                        "package": "omnigent",
                        "version": "0.11.0",
                        "digest": "sha256:" + "c" * 64,
                        "pluginEntryPoint": None,
                    },
                    "runtimeRequirements": {},
                    "capabilities": {
                        "integrationMode": "native-server",
                        "authModel": "own-auth",
                        "interrupt": True,
                        "streaming": True,
                    },
                    "setupSteps": [],
                }
            ],
        )
        harness = catalog.harnesses[0]
        await DbHarnessCatalogRepository(sessions).persist(
            HarnessCatalogSyncResult(
                snapshot=catalog,
                trust_records=(
                    classify_harness_trust(
                        harnessId=harness.id,
                        implementation=harness.implementation,
                        trustState=TrustState.core_trusted,
                    ),
                ),
                diagnostics={},
            )
        )
        artifacts = _service(session, tmp_path)
        skills = AgentSkillsService(session, artifact_service=artifacts)
        await skills.create_skill(slug="retained-deployment-skill", title="Saved Skill")
        frozen_content = "# Saved Skill\nUse this admitted content.\n"
        skill = await skills.update_skill_content(
            skill_slug="retained-deployment-skill",
            content=frozen_content,
            principal="user-1",
        )
        frozen_content_ref = skill.artifact_ref
        task = await _complete_artifact(
            artifacts,
            principal="user-1",
            payload=b'{"instructions":"Use this admitted content."}',
            content_type="application/json",
        )
        frozen = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=SessionExecutionPlanStore(session),
            session_factory=sessions,
            workflow_id="mm-schedule:saved-deployment-skill",
            task_input_snapshot_ref=task.artifact_id,
            task_input_snapshot_digest="sha256:" + task.sha256,
            extra_parameters={
                "workflow": {
                    "instructions": "Use this admitted content.",
                    "skill": {"id": "retained-deployment-skill"},
                }
            },
        )
        schedules = RecurringWorkflowsService(
            session,
            temporal_client_adapter=SimpleNamespace(create_schedule=AsyncMock()),
            artifact_service=artifacts,
        )
        _profile, profile_bytes = await artifacts.read(
            artifact_id=frozen.envelope.payload.agentProfileSnapshotRef.removeprefix(
                "artifact:"
            ),
            principal="user-1",
        )
        profile_snapshot = json.loads(profile_bytes)
        authority_refs = [task.artifact_id, *frozen.artifact_refs]
        if legacy_incomplete:
            # The original compiler returned newly persisted bundles only,
            # omitting this already-persisted deployment Skill revision.
            authority_refs = [
                ref for ref in authority_refs if ref != frozen_content_ref
            ]
        definition = await schedules.create_definition(
            name="Saved deployment Skill",
            description=None,
            enabled=enabled,
            schedule_type="cron",
            cron="0 * * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=None,
            target={
                "workflowType": "MoonMind.UserWorkflow",
                "agentProfileSnapshot": profile_snapshot,
                "initialParameters": {
                    "targetRuntime": "omnigent",
                    "agentProfileSnapshot": profile_snapshot,
                    "omnigentExecutionPlan": frozen.binding.model_dump(
                        mode="json", by_alias=True
                    ),
                    "resolvedSkillsetRef": frozen.resolved_skillset_ref,
                },
                "omnigentAuthorityArtifactRefs": authority_refs,
            },
            policy={},
            actor=SimpleNamespace(id="user-1"),
        )
        updated = await skills.update_skill_content(
            skill_slug="retained-deployment-skill",
            content="# Saved Skill\nDifferent content for future admissions.\n",
            principal="user-1",
        )
        updated_content_ref = updated.artifact_ref
        assert updated_content_ref != frozen_content_ref
        assert definition.enabled is enabled
        unreferenced = await expired_input(artifacts)
        if legacy_incomplete:
            original_parameters = dict(definition.target["initialParameters"])
            outcome = await schedules.refresh_managed_bootstrap_schedules()
            # The selected provider is unavailable in this database. Retention
            # repair still commits before mutable launch-authority refresh.
            assert len(outcome.failures) == 1
            await session.refresh(definition)
            assert definition.target["initialParameters"] == original_parameters

        result = await artifacts.sweep_lifecycle(
            principal="service:sweeper", now=FUTURE
        )

        # The saved plan names the original bytes after its current deployment
        # definition moves; the new revision is still current saved work too.
        assert result.soft_deleted_count == 1
        assert (
            await artifacts.read(artifact_id=frozen_content_ref, principal="user-1")
        )[1] == frozen_content.encode()
        assert (
            await session.get(models.TemporalArtifact, unreferenced.artifact_id)
        ).status is models.TemporalArtifactStatus.DELETED
        assert (
            await artifacts.read(artifact_id=updated_content_ref, principal="user-1")
        )[1] == b"# Saved Skill\nDifferent content for future admissions.\n"


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
