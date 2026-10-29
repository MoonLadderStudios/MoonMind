"""API input grants and saved-schedule retention share PostgreSQL row locks."""

import asyncio
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api_service.api.routers import executions
from api_service.db.models import RecurringWorkflowDefinition, TemporalArtifactLink
from api_service.services import omnigent_execution_plan_service as plans
from api_service.services import recurring_workflows_service as recurring
from api_service.services.agent_skills_service import AgentSkillsService
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.stores import SessionExecutionPlanStore
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.artifact_access import access_for_test
from tests.integration import test_execution_input_admission_postgres as admission
from tests.integration.test_execution_start_admission import _submit
from tests.unit.omnigent.test_admitted_input_authority import _put
from tests.unit.omnigent.test_api_planning_input_authority import _api_context
from tests.unit.omnigent.test_plan_and_realizer_dispatch import _codex_plan_inputs

sessions = admission.sessions
conversion_postgres_url = admission.conversion_postgres_url
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def test_api_create_and_saved_schedule_edit_share_inputs_without_deadlock(
    sessions, monkeypatch
):
    await _api_context(sessions, monkeypatch)
    snapshot = executions.resolve_default_agent_profile_snapshot.return_value
    monkeypatch.setattr(
        recurring,
        "resolve_default_agent_profile_snapshot",
        AsyncMock(return_value=snapshot),
    )
    skill_body = b"# Shared Skill\nInspect the admitted input.\n"
    async with sessions() as session:
        artifacts = TemporalArtifactService(TemporalArtifactRepository(session))
        skills = AgentSkillsService(session, artifact_service=artifacts)
        await skills.create_skill(slug="shared-lock-skill", title="Shared Skill")
        skill = await skills.update_skill_content(
            skill_slug="shared-lock-skill",
            content=skill_body.decode(),
            principal="system",
        )
        skill_id = skill.artifact_ref
    input_body = b'{"instructions":"Read this saved input"}'
    input_id = await _put(
        sessions,
        input_body,
        owner="system",
        workflow_id=None,
        link_type="",
        content_type="application/json",
    )
    assert skill_id < input_id

    async def compile_selected_runtime(**kwargs):
        # Keep installed-host selection controlled, as in the existing API
        # boundary fixture. Deployment Skill resolution, immutable manifests,
        # core compilation, artifact writes and plan storage remain real.
        resolved, manifest_ref, digest, content_refs = (
            await plans._resolve_and_persist_skills(
                session_factory=sessions,
                db_session=kwargs["db_session"],
                artifact_service=kwargs["artifact_service"],
                principal=kwargs["principal"],
                workflow_id=kwargs["workflow_id"],
                task_input_snapshot_digest=kwargs["task_input_snapshot_digest"],
                initial_parameters=kwargs["initial_parameters"],
            )
        )
        assert [entry.skill_name for entry in resolved.skills] == ["shared-lock-skill"]
        assert content_refs == (skill_id,)
        inputs = _codex_plan_inputs()
        inputs["resolved_skills"] = {
            "resolvedSkillSetRef": f"artifact:{manifest_ref}",
            "resolvedSkillSetDigest": digest,
            "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
        }
        inputs["execution_authority"] = {
            "authoredRequestRef": kwargs["authored_request_ref"],
            "authoredRequestDigest": kwargs["authored_request_digest"],
            "taskInputSnapshotRef": kwargs["task_input_snapshot_ref"],
            "taskInputSnapshotDigest": kwargs["task_input_snapshot_digest"],
            **{
                key: "policy:retained"
                for key in (
                    "repositoryIntentRef",
                    "continuationPolicyRef",
                    "remediationPolicyRef",
                    "checkpointPolicyRef",
                    "publicationPolicyRef",
                    "timingPolicyRef",
                    "failurePolicyRef",
                )
            },
        }
        store = kwargs.get("execution_plan_store") or SessionExecutionPlanStore(
            kwargs["db_session"]
        )
        plan = await store.persist(compile_execution_plan(**inputs))
        envelope_ref, _digest = await plans.persist_json_artifact(
            artifact_service=kwargs["artifact_service"],
            principal=kwargs["principal"],
            artifact_class="omnigent.execution_plan",
            payload=plan.model_dump(mode="json", by_alias=True),
        )
        return plans.PersistedOmnigentExecutionPlan(
            artifact_access=kwargs["access"],
            envelope=plan,
            binding=OmnigentExecutionPlanBinding(
                planRef=plan.planRef,
                planDigest=plan.planRef.removeprefix("omnigent-execution-plan:"),
                planArtifactRef=envelope_ref,
                taskInputSnapshotRef=kwargs["task_input_snapshot_ref"],
                taskInputSnapshotDigest=kwargs["task_input_snapshot_digest"],
            ),
            artifact_refs=(envelope_ref, manifest_ref, *content_refs),
            resolved_skillset_ref=manifest_ref,
            resolved_skillset_digest=digest,
        )

    monkeypatch.setattr(
        plans, "compile_and_persist_execution_plan", compile_selected_runtime
    )
    workflow = {
        "instructions": "Read the saved input using the shared Skill.",
        "skill": {"id": "shared-lock-skill"},
        "publish": {"mode": "none"},
    }
    async with sessions() as session:
        owner = recurring.RecurringWorkflowsService(
            session,
            artifact_service=TemporalArtifactService(
                TemporalArtifactRepository(session)
            ),
            temporal_client_adapter=SimpleNamespace(create_schedule=AsyncMock()),
        )
        definition = await owner.create_definition(
            name="Saved shared inputs",
            description=None,
            enabled=False,
            schedule_type="cron",
            cron="0 * * * *",
            timezone="UTC",
            scope_type="personal",
            scope_ref=None,
            owner_user_id=None,
            target={
                "workflowType": "MoonMind.UserWorkflow",
                "inputArtifactRef": input_id,
                "initialParameters": {
                    "targetRuntime": "omnigent",
                    "workflow": workflow,
                },
            },
            policy={},
            actor=OperatorAdmission(via="loopback"),
        )
        definition_id, version = definition.id, definition.version
        assert skill_id in definition.target["omnigentAuthorityArtifactRefs"]
        # Only already-retained inputs become sweep candidates. Newly admitted
        # execution artifacts keep their normal lifetimes.
        for artifact_id in (skill_id, input_id):
            artifact = await TemporalArtifactRepository(session).get_artifact(
                artifact_id
            )
            artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    role = ContextVar("admission_retention_role", default="")
    retained_skill, admitted_input = asyncio.Event(), asyncio.Event()
    api_shared_lock_attempt = asyncio.Event()
    sweep_completed = asyncio.Event()
    original_retain = recurring.RecurringWorkflowsService._retain_target_artifacts
    original_create = TemporalExecutionService.create_execution
    original_lock = TemporalArtifactRepository.get_artifact_for_update

    async def retain(self, target):
        token = role.set("retention")
        try:
            return await original_retain(self, target)
        finally:
            role.reset(token)

    async def create(self, **kwargs):
        token = role.set("api")
        try:
            return await original_create(self, **kwargs)
        finally:
            role.reset(token)

    async def lock(self, artifact_id):
        canonical_api = role.get() == "api"
        if canonical_api and artifact_id == skill_id:
            # A globally ordered admission may wait on the Skill first. Let
            # retention finish in that case instead of creating a test-only
            # wait; the old input-first admission signals after acquiring it.
            api_shared_lock_attempt.set()
        artifact = await original_lock(self, artifact_id)
        if role.get() == "retention" and artifact_id == skill_id:
            retained_skill.set()
            await asyncio.wait_for(api_shared_lock_attempt.wait(), 15)
            await asyncio.wait_for(sweep_completed.wait(), 15)
        elif canonical_api and artifact_id == input_id:
            admitted_input.set()
            api_shared_lock_attempt.set()
            await asyncio.wait_for(retained_skill.wait(), 15)
        return artifact

    monkeypatch.setattr(
        recurring.RecurringWorkflowsService, "_retain_target_artifacts", retain
    )
    monkeypatch.setattr(TemporalExecutionService, "create_execution", create)
    monkeypatch.setattr(TemporalArtifactRepository, "get_artifact_for_update", lock)

    async def edit_schedule():
        async with sessions() as session:
            definition = await session.get(RecurringWorkflowDefinition, definition_id)
            return await recurring.RecurringWorkflowsService(
                session,
                artifact_service=TemporalArtifactService(
                    TemporalArtifactRepository(session)
                ),
                temporal_client_adapter=SimpleNamespace(update_schedule=AsyncMock()),
            ).update_definition(
                definition,
                name="Renamed saved inputs",
                target=deepcopy(definition.target),
                expected_version=version,
                actor=OperatorAdmission(via="loopback"),
            )

    async def sweep_retention():
        await asyncio.wait_for(retained_skill.wait(), 15)
        token = role.set("sweep")
        try:
            async with sessions() as session:
                return await TemporalArtifactService(
                    TemporalArtifactRepository(session)
                ).sweep_lifecycle(principal="service:lifecycle")
        finally:
            sweep_completed.set()
            role.reset(token)

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(
            return_value=SimpleNamespace(run_id="shared-input-run")
        ),
        describe_workflow=AsyncMock(return_value=None),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            edit_schedule(),
            _submit(
                sessions,
                adapter,
                {
                    "type": "workflow",
                    "payload": {
                        "inputArtifactRef": input_id,
                        "workflow": deepcopy(workflow),
                        "idempotencyKey": "shared-saved-input-admission",
                    },
                },
            ),
            sweep_retention(),
            return_exceptions=True,
        ),
        45,
    )
    assert retained_skill.is_set() and admitted_input.is_set(), outcomes
    assert sweep_completed.is_set(), outcomes
    errors = [
        str(outcome) for outcome in outcomes if isinstance(outcome, BaseException)
    ]
    assert not errors, "\n".join(errors)
    adapter.start_workflow.assert_awaited_once()
    execution = outcomes[1]
    assert outcomes[2].soft_deleted_count == 0
    # Saved-definition protection excludes both expired inputs before locking.
    assert outcomes[2].expired_candidate_count == 0
    async with sessions() as session:
        definition = await session.get(RecurringWorkflowDefinition, definition_id)
        assert definition.name == "Renamed saved inputs"
        assert definition.version == version + 1
        reader = TemporalArtifactService(TemporalArtifactRepository(session))
        for artifact_id, expected_body in (
            (input_id, input_body),
            (skill_id, skill_body),
        ):
            _artifact, body = await reader.read(
                artifact_id=artifact_id,
                access=access_for_test(f"workflow:{execution.workflow_id}"),
            )
            assert body == expected_body
            assert (
                await session.scalars(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.artifact_id == artifact_id,
                        TemporalArtifactLink.workflow_id == execution.workflow_id,
                    )
                )
            ).all()
