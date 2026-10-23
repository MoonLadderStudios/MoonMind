"""A Temporal-started occurrence can consume its immutable authored inputs."""

import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from api_service.db import base as db_base
from api_service.db.models import (
    ManagedAgentProviderProfile,
    TemporalArtifact,
    TemporalArtifactLink,
)
from api_service.services import omnigent_execution_plan_service as plan_service
from api_service.services.recurring_workflows_service import RecurringWorkflowsService
from moonmind.omnigent.bridge_artifacts import link_verified_execution_plan_inputs
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.workflows.temporal.activities import omnigent_activities
from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalPlanActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.omnigent import test_admitted_input_authority as authority_fixtures
from tests.unit.omnigent.test_admitted_input_authority import (
    _put,
    _skill_set,
)
from tests.unit.omnigent.test_plan_and_realizer_dispatch import _codex_plan_inputs

pytestmark = pytest.mark.asyncio
OWNER = "00000000-0000-0000-0000-000000000000"
DEFINITION = UUID("a81dd46f-d6a2-4b70-96bf-7943dba4b846")
WORKFLOW = f"mm:{DEFINITION}-2026-10-23T20:48:17Z"
RUN = "scheduled-run"
artifacts = authority_fixtures.artifacts
auth_mode = authority_fixtures.auth_mode


async def _schedule(sessions):
    """Use the schedule action producer and durable plan/artifact stores."""
    input_id = await _put(
        sessions,
        b'{"instructions":"Inspect the repository"}',
        owner=OWNER,
        workflow_id=None,
        link_type="",
        content_type="application/json",
    )
    plan_id = await _put(
        sessions,
        b'{"saved":"plan"}',
        owner=OWNER,
        workflow_id=None,
        link_type="",
        content_type="application/json",
    )
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "inputArtifactRef": input_id,
        "planArtifactRef": plan_id,
        "initialParameters": {
            "targetRuntime": "omnigent",
            "inputArtifactRef": input_id,
            "repository": "MoonLadderStudios/Tactics",
            "task": {"tool": {"type": "skill", "name": "auto"}},
        },
    }
    frozen = json.dumps(
        {
            "snapshotVersion": "original-task-input/v1",
            "source": {"kind": "schedule", "definitionId": str(DEFINITION)},
            "target": target,
        }
    ).encode()
    snapshot_id = await _put(
        sessions,
        frozen,
        owner=OWNER,
        workflow_id=None,
        link_type="",
        content_type="application/json",
        metadata={"artifact_class": "original_task_input_snapshot"},
    )
    skills = await _skill_set(sessions, tag="scheduled", owner=OWNER, workflow_id=None)
    inputs = _codex_plan_inputs()
    inputs["resolved_skills"] = {
        "resolvedSkillSetRef": f"artifact:{skills.manifest_id}",
        "resolvedSkillSetDigest": "sha256:" + "a" * 64,
        "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
    }
    digest = "sha256:" + hashlib.sha256(frozen).hexdigest()
    inputs["execution_authority"] = {
        "authoredRequestRef": f"artifact:{snapshot_id}",
        "authoredRequestDigest": digest,
        "taskInputSnapshotRef": f"artifact:{snapshot_id}",
        "taskInputSnapshotDigest": digest,
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
    plan = await DbExecutionPlanStore(sessions).persist(
        compile_execution_plan(**inputs)
    )
    plan_bytes = json.dumps(
        plan.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    envelope_id = await _put(
        sessions,
        plan_bytes,
        owner=OWNER,
        workflow_id=None,
        link_type="",
        content_type="application/json",
        metadata={"artifact_class": "omnigent.execution_plan"},
    )
    binding = OmnigentExecutionPlanBinding(
        planRef=plan.planRef,
        planDigest=plan.planRef.removeprefix("omnigent-execution-plan:"),
        planArtifactRef=f"artifact:{envelope_id}",
        taskInputSnapshotRef=f"artifact:{snapshot_id}",
        taskInputSnapshotDigest=digest,
    )
    target["initialParameters"]["omnigentExecutionPlan"] = binding.model_dump(
        mode="json", by_alias=True
    )
    _, action = RecurringWorkflowsService(None)._workflow_bundle_for_target(
        definition_id=DEFINITION,
        name="Codex Tactics Search",
        owner_user_id=None,
        target_payload=target,
    )
    return SimpleNamespace(
        action=action,
        input_id=input_id,
        plan_id=plan_id,
        snapshot_id=snapshot_id,
        binding=binding,
        skills=skills,
        compile_inputs=inputs,
    )


def _activity_context(monkeypatch, sessions):
    monkeypatch.setattr(db_base, "async_session_maker", sessions)
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id=WORKFLOW,
            workflow_run_id=RUN,
            namespace="default",
        ),
    )


async def _admit(action, *, workflow_id=WORKFLOW):
    return await omnigent_activities.omnigent_admit_scheduled_occurrence_plan_activity(
        {
            "workflowId": workflow_id,
            "initialParameters": action["initial_parameters"],
        }
    )


async def test_scheduled_admission_reaches_real_planner_and_retained_plan(
    artifacts, monkeypatch
):
    saved = await _schedule(artifacts)
    _activity_context(monkeypatch, artifacts)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=saved.input_id, principal=f"workflow:{WORKFLOW}"
            )
    await _admit(saved.action)
    await _admit(saved.action)  # Activity retry must reuse the same grants.
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        planner = TemporalPlanActivities(
            artifact_service=service, planner=_build_runtime_planner()
        )
        result = await planner.plan_generate(
            principal=f"workflow:{WORKFLOW}",
            inputs_ref=saved.action["input_artifact_ref"],
            parameters=saved.action["initial_parameters"],
        )
        assert result.plan_ref.artifact_id
        _, retained = await service.read(
            artifact_id=saved.plan_id, principal=f"workflow:{WORKFLOW}"
        )
        assert json.loads(retained) == {"saved": "plan"}
        _, manifest = await service.read(
            artifact_id=saved.skills.manifest_id, principal=f"workflow:{WORKFLOW}"
        )
        assert json.loads(manifest)["skills"][0]["content_ref"] == saved.skills.body_id
        _, skill_body = await service.read(
            artifact_id=saved.skills.body_id, principal=f"workflow:{WORKFLOW}"
        )
        assert skill_body == saved.skills.body
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=saved.input_id, principal="workflow:unrelated"
            )
        links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.artifact_id.in_(
                            [saved.input_id, saved.plan_id]
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == 2
        assert {(link.workflow_id, link.run_id) for link in links} == {(WORKFLOW, RUN)}


async def test_verified_recovery_keeps_schedule_inputs_with_new_workflow_id(artifacts):
    saved = await _schedule(artifacts)
    recovery_id = "mm:authorized-recovery"
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=saved.input_id, principal=f"workflow:{recovery_id}"
            )
    plan = await DbExecutionPlanStore(artifacts).load(saved.binding.plan_ref)
    await link_verified_execution_plan_inputs(
        session_factory=artifacts,
        plan=plan,
        binding=saved.binding,
        workflow_id=recovery_id,
        run_id="recovery-run",
    )
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        _, body = await service.read(
            artifact_id=saved.input_id, principal=f"workflow:{recovery_id}"
        )
        assert json.loads(body)["instructions"] == "Inspect the repository"
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=saved.input_id, principal="workflow:unrelated"
            )


async def test_scheduled_admission_uses_temporal_identity_without_a_name_convention(
    artifacts, monkeypatch
):
    saved = await _schedule(artifacts)
    _activity_context(monkeypatch, artifacts)
    workflow_id = "retained:schedule:occurrence"
    monkeypatch.setattr(
        omnigent_activities.activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id=workflow_id,
            workflow_run_id=RUN,
            namespace="default",
        ),
    )
    await _admit(saved.action, workflow_id=workflow_id)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        _, body = await service.read(
            artifact_id=saved.input_id, principal=f"workflow:{workflow_id}"
        )
        assert json.loads(body)["instructions"] == "Inspect the repository"


async def test_fresh_admission_links_new_skillset_before_workflow_consumer(
    artifacts, monkeypatch
):
    saved = await _schedule(artifacts)
    _activity_context(monkeypatch, artifacts)
    async with artifacts() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="codex", runtime_id="codex_cli", provider_id="openai"
            )
        )
        await session.commit()
    parameters = saved.action["initial_parameters"]
    parameters["agentProfileSnapshot"] = {"providerProfileRef": "codex"}
    fresh_skills = await _skill_set(
        artifacts, tag="new-installed-runtime", owner=OWNER, workflow_id=None
    )

    async def compile_installed_runtime(**kwargs):
        # Simulate installed-host selection, retaining the real plan compiler,
        # immutable authority, artifact serialization and durable plan store.
        assert kwargs["binding"] == saved.binding
        inputs = {
            **saved.compile_inputs,
            "resolved_skills": {
                **saved.compile_inputs["resolved_skills"],
                "resolvedSkillSetRef": f"artifact:{fresh_skills.manifest_id}",
            },
        }
        plan = await DbExecutionPlanStore(artifacts).persist(
            compile_execution_plan(**inputs)
        )
        body = json.dumps(
            plan.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        plan_id = await _put(
            artifacts,
            body,
            owner=OWNER,
            workflow_id=None,
            link_type="",
            metadata={"artifact_class": "omnigent.execution_plan"},
        )
        binding = saved.binding.model_copy(
            update={
                "plan_ref": plan.planRef,
                "plan_digest": plan.planRef.removeprefix("omnigent-execution-plan:"),
                "plan_artifact_ref": f"artifact:{plan_id}",
            }
        )
        return plan_service.PersistedOmnigentExecutionPlan(
            envelope=plan,
            binding=binding,
            artifact_refs=(plan_id,),
            resolved_skillset_ref=fresh_skills.manifest_id,
            resolved_skillset_digest="sha256:" + "a" * 64,
        )

    monkeypatch.setattr(
        plan_service, "compile_frozen_execution_plan", compile_installed_runtime
    )
    admitted = await _admit(saved.action)
    assert admitted["disposition"] == "admitted"
    workflow = run_module.MoonMindRunWorkflow()
    workflow._original_input_payload = {"initial_parameters": dict(parameters)}
    workflow._adopt_admitted_execution_plan(
        parameters, admitted, subject="Scheduled occurrence"
    )
    assert parameters["resolvedSkillsetRef"] == fresh_skills.manifest_id
    assert parameters["omnigentExecutionPlan"] != saved.binding.model_dump(
        mode="json", by_alias=True
    )
    monkeypatch.setattr(
        run_module.workflow, "info", lambda: SimpleNamespace(workflow_id=WORKFLOW)
    )
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        catalog = build_default_activity_catalog()
        selected = TemporalActivityCatalog(
            activities=tuple(
                a for a in catalog.activities if a.activity_type == "artifact.read"
            ),
            fleets=catalog.fleets,
        )
        (binding,) = build_activity_bindings(
            selected, artifact_activities=TemporalArtifactActivities(service)
        )
        reads = []

        async def execute(name, request, **kwargs):
            reads.append(request.artifact_ref)
            assert name == "artifact.read"
            return await binding.handler(request)

        monkeypatch.setattr(run_module, "execute_typed_activity", execute)
        result = await workflow._resolve_agent_node_skillset_ref(
            task_skills=None,
            node_inputs={"selectedSkill": "admitted-skill"},
            node_id="assess",
            existing_skillset_ref=parameters["resolvedSkillsetRef"],
        )
        assert result == fresh_skills.manifest_id
        assert len(reads) == 1
        planner = TemporalPlanActivities(
            artifact_service=service, planner=_build_runtime_planner()
        )
        assert (
            await planner.plan_generate(
                principal=f"workflow:{WORKFLOW}",
                inputs_ref=saved.input_id,
                parameters=parameters,
            )
        ).plan_ref.artifact_id
        _, body = await service.read(
            artifact_id=fresh_skills.body_id, principal=f"workflow:{WORKFLOW}"
        )
        assert body == fresh_skills.body


@pytest.mark.parametrize(
    "corruption",
    [
        "body",
        "binding",
        "wrong-input",
        "wrong-workflow",
        "unreadable",
        "plan-pointer",
        "definition",
    ],
)
async def test_scheduled_admission_refuses_changed_authority(
    artifacts, monkeypatch, corruption
):
    saved = await _schedule(artifacts)
    _activity_context(monkeypatch, artifacts)
    if corruption == "body":
        async with artifacts() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            artifact = await session.get(TemporalArtifact, saved.snapshot_id)
            # Corrupt the original bytes while leaving their recorded digest intact.
            service._store.read_path(artifact.storage_key).write_bytes(b"{}")
    elif corruption == "binding":
        saved.action["initial_parameters"]["omnigentExecutionPlan"][
            "taskInputSnapshotDigest"
        ] = ("sha256:" + "f" * 64)
    elif corruption == "wrong-input":
        saved.action["initial_parameters"]["inputArtifactRef"] = "art_unrelated"
    elif corruption == "unreadable":
        async with artifacts() as session:
            artifact = await session.get(TemporalArtifact, saved.snapshot_id)
            artifact.created_by_principal = "workflow:source-without-input-access"
            await session.commit()
    elif corruption == "plan-pointer":
        saved.action["initial_parameters"]["omnigentExecutionPlan"][
            "planArtifactRef"
        ] = saved.input_id
    elif corruption == "definition":
        saved.action["initial_parameters"]["system"]["recurrence"][
            "definitionId"
        ] = "another-definition"
    else:
        monkeypatch.setattr(
            omnigent_activities.activity,
            "info",
            lambda: SimpleNamespace(
                workflow_id="workflow:unrelated",
                workflow_run_id=RUN,
                namespace="default",
            ),
        )
    with pytest.raises(
        (ValueError, RuntimeError, TemporalArtifactError),
        match="authority|snapshot|workflow|input",
    ):
        await _admit(saved.action)
    async with artifacts() as session:
        links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.artifact_id == saved.input_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert links == []


@pytest.mark.parametrize("target", ["input", "manifest"])
@pytest.mark.parametrize("protection", ["quarantined", "restricted"])
async def test_admission_does_not_bypass_protected_raw_inputs(
    artifacts, monkeypatch, target, protection
):
    saved = await _schedule(artifacts)
    _activity_context(monkeypatch, artifacts)
    artifact_id = saved.input_id if target == "input" else saved.skills.manifest_id
    await authority_fixtures._protect(artifacts, artifact_id, protection)
    if target == "input" and protection == "restricted":
        await _admit(saved.action)
    else:
        with pytest.raises(
            (RuntimeError, TemporalArtifactError), match="quarantined|restricted"
        ):
            await _admit(saved.action)
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        # A quarantined root was never linked, so authorization rejects its
        # reader before the raw quarantine gate. A linked manifest reaches it.
        if protection == "restricted" or target == "input":
            expected_error = TemporalArtifactAuthorizationError
            expected_message = (
                "restricted" if protection == "restricted" else "cannot read"
            )
        else:
            expected_error = TemporalArtifactStateError
            expected_message = "quarantined"
        with pytest.raises(expected_error, match=expected_message):
            await TemporalArtifactActivities(service).artifact_read(
                {
                    "principal": f"workflow:{WORKFLOW}",
                    "artifact_ref": artifact_id,
                }
            )
