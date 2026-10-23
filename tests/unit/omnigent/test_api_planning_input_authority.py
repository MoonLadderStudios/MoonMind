"""Normal API admission delegates immutable inputs before Temporal can read."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api_service.api.routers import executions
from api_service.db import base as db_base
from api_service.db.models import (
    ManagedAgentProviderProfile,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactRedactionLevel,
    TemporalExecutionCanonicalRecord,
)
from api_service.services import omnigent_execution_plan_service as plan_service
from moonmind.config.settings import settings
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.activity_runtime import TemporalPlanActivities
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactActivities,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from tests.unit.omnigent import test_admitted_input_authority as _authority_fixtures
from tests.unit.omnigent.test_admitted_input_authority import (
    _put,
    _skill_set,
)
from tests.unit.omnigent.test_plan_and_realizer_dispatch import _codex_plan_inputs

pytestmark = pytest.mark.asyncio
artifacts = _authority_fixtures.artifacts
auth_mode = _authority_fixtures.auth_mode


async def test_api_original_snapshot_keeps_its_explicit_planning_pointers():
    parameters = {
        "workflow": {"instructions": "Read admitted artifacts"},
        "inputArtifactRef": "art_input",
        "planArtifactRef": "art_plan",
    }
    payload, task = executions._snapshot_source_payload_from_parameters(parameters)
    snapshot = executions._build_original_workflow_input_snapshot_payload(
        source_kind="create", payload=payload, task_payload=task
    )
    assert snapshot["draft"]["inputArtifactRef"] == "art_input"
    assert snapshot["draft"]["planArtifactRef"] == "art_plan"


async def _api_context(sessions, monkeypatch, *, corruption=None):
    actor = executions._execution_principal(OperatorAdmission(via="loopback"))
    skills = await _skill_set(sessions, tag="api-create", owner=actor, workflow_id=None)
    profile = ManagedAgentProviderProfile(
        profile_id="codex",
        runtime_id="codex_cli",
        provider_id="openai",
        default_model="gpt-5",
        auth_state="connected",
        last_auth_method="oauth_volume",
        credential_source="oauth_volume",
        runtime_materialization_mode="oauth_home",
        volume_ref="test-only-auth",
        volume_mount_path="/tmp/auth",
        enabled=True,
    )
    async with sessions() as session:
        session.add(profile)
        await session.commit()
    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "default",
        "version": 1,
        "digest": "sha256:" + "a" * 64,
        "providerProfileRef": "codex",
        "executionProfileRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "agentId": "codex-native",
        "document": {"model": {"settings": {}}, "workspace": {}, "capture": {}},
    }
    monkeypatch.setattr(db_base, "async_session_maker", sessions)
    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    monkeypatch.setattr(
        executions,
        "resolve_default_agent_profile_snapshot",
        AsyncMock(return_value=snapshot),
    )
    compiled = []

    async def compile_selected_runtime(**kwargs):
        # Installed-host/catalog selection is controlled. Core compilation,
        # frozen API snapshot, plan storage and artifact bytes remain real.
        inputs = _codex_plan_inputs()
        inputs["resolved_skills"] = {
            "resolvedSkillSetRef": f"artifact:{skills.manifest_id}",
            "resolvedSkillSetDigest": "sha256:" + "a" * 64,
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
        plan = await DbExecutionPlanStore(sessions).persist(
            compile_execution_plan(**inputs)
        )
        body = json.dumps(
            plan.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        envelope_id = await _put(
            sessions,
            body,
            owner=actor,
            workflow_id=None,
            link_type="",
            metadata={"artifact_class": "omnigent.execution_plan"},
        )
        binding = OmnigentExecutionPlanBinding(
            planRef=plan.planRef,
            planDigest=plan.planRef.removeprefix("omnigent-execution-plan:"),
            planArtifactRef=f"artifact:{envelope_id}",
            taskInputSnapshotRef=kwargs["task_input_snapshot_ref"],
            taskInputSnapshotDigest=kwargs["task_input_snapshot_digest"],
        )
        if corruption == "binding":
            binding = binding.model_copy(update={"plan_digest": "sha256:" + "f" * 64})
        result = plan_service.PersistedOmnigentExecutionPlan(
            envelope=plan,
            binding=binding,
            artifact_refs=(envelope_id, skills.manifest_id),
            resolved_skillset_ref=skills.manifest_id,
            resolved_skillset_digest="sha256:" + "a" * 64,
        )
        compiled.append(result)
        return result

    monkeypatch.setattr(
        plan_service, "compile_and_persist_execution_plan", compile_selected_runtime
    )
    return SimpleNamespace(actor=actor, skills=skills, compiled=compiled)


@pytest.mark.parametrize(
    "include_roots,lost_ack", [(False, False), (True, False), (True, True)]
)
async def test_normal_api_manifest_is_readable_at_the_first_temporal_consumer(
    artifacts, monkeypatch, include_roots, lost_ack
):
    saved = await _api_context(artifacts, monkeypatch)
    observed = []
    failures = []

    async def start_workflow(**kwargs):
        # Temporal may start executing before start_workflow returns. This is
        # the same artifact.read implementation the parent uses before AgentRun.
        async with artifacts() as session:
            reader = TemporalArtifactActivities(
                TemporalArtifactService(TemporalArtifactRepository(session))
            )
            try:
                body = await reader.artifact_read(
                    {
                        "principal": f"workflow:{kwargs['workflow_id']}",
                        "artifact_ref": kwargs["input_args"]["initial_parameters"][
                            "resolvedSkillsetRef"
                        ],
                    }
                )
                observed.append(json.loads(body))
                if include_roots:
                    planner = TemporalPlanActivities(
                        artifact_service=reader._service,
                        planner=_build_runtime_planner(),
                    )
                    result = await planner.plan_generate(
                        principal=f"workflow:{kwargs['workflow_id']}",
                        inputs_ref=kwargs["input_args"]["input_artifact_ref"],
                        parameters=kwargs["input_args"]["initial_parameters"],
                    )
                    assert result.plan_ref.artifact_id
                    plan = await reader.artifact_read(
                        {
                            "principal": f"workflow:{kwargs['workflow_id']}",
                            "artifact_ref": kwargs["input_args"]["plan_artifact_ref"],
                        }
                    )
                    assert json.loads(plan) == {"saved": "plan"}
            except Exception as exc:
                failures.append(exc)
                raise
        if lost_ack:
            raise ConnectionError(
                "Start acknowledgement was lost after the consumer ran"
            )
        return SimpleNamespace(run_id="actual-api-run")

    adapter = SimpleNamespace(
        start_workflow=AsyncMock(side_effect=start_workflow),
        describe_workflow=AsyncMock(return_value=SimpleNamespace(run_id="actual-api-run")),
        namespace_retention_seconds=AsyncMock(return_value=86400),
    )
    roots = {}
    if include_roots:
        for key, body in (
            ("inputArtifactRef", b'{"instructions":"Inspect the repository"}'),
            ("planArtifactRef", b'{"saved":"plan"}'),
        ):
            roots[key] = await _put(
                artifacts,
                body,
                owner=saved.actor,
                workflow_id=None,
                link_type="",
                content_type="application/json",
            )
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        monkeypatch.setattr(service, "_enforce_worker_code_freshness", AsyncMock())
        payload = {
            "type": "workflow",
            "payload": {
                **roots,
                "idempotencyKey": "api-input-authority",
                "workflow": {
                    "instructions": "Inspect the repository",
                    "publish": {"mode": "none"},
                },
            },
        }
        response = await executions.create_execution(
            payload=payload,
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
        )
        assert failures == []
        assert observed
        assert observed[0]["skills"][0]["content_ref"] == saved.skills.body_id
        links = (
            (
                await session.execute(
                    select(TemporalArtifactLink).where(
                        TemporalArtifactLink.workflow_id == response.workflow_id,
                        TemporalArtifactLink.link_type == "input.execution_plan",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert links
        assert {link.run_id for link in links} == {response.run_id}
        if not lost_ack:
            assert response.run_id == "actual-api-run"
        repeated = await executions.create_execution(
            payload=payload,
            service=service,
            session=session,
            user=OperatorAdmission(via="loopback"),
            _submit_enabled=None,
            principal_context={},
            authorization=None,
            execution_fanout=None,
        )
        assert repeated.workflow_id == response.workflow_id
        adapter.start_workflow.assert_awaited_once()
        assert len(saved.compiled) == 1
        reader = TemporalArtifactService(TemporalArtifactRepository(session))
        _, manifest = await reader.read(
            artifact_id=saved.skills.manifest_id,
            principal=f"workflow:{response.workflow_id}",
        )
        assert json.loads(manifest) == observed[0]
        with pytest.raises(TemporalArtifactAuthorizationError):
            await reader.read(
                artifact_id=saved.skills.manifest_id, principal="workflow:unrelated"
            )


@pytest.mark.parametrize(
    "corruption",
    [
        "binding",
        "quarantined-input",
        "quarantined-plan",
        "unreadable-input",
        "unreadable-plan",
    ],
)
async def test_api_refuses_invalid_or_unreadable_authority_before_start(
    artifacts, monkeypatch, corruption
):
    saved = await _api_context(artifacts, monkeypatch, corruption=corruption)
    roots = {}
    if corruption != "binding":
        protection, target = corruption.split("-")
        root = await _put(
            artifacts,
            b'{"instructions":"Retain protected input"}',
            owner=(saved.actor if protection == "quarantined" else "workflow:another"),
            workflow_id=None,
            link_type="",
            content_type="application/json",
        )
        roots[("inputArtifactRef" if target == "input" else "planArtifactRef")] = root
        async with artifacts() as session:
            artifact = await session.get(TemporalArtifact, root)
            if protection == "quarantined":
                artifact.metadata_json = {"quarantine": True}
            else:
                artifact.redaction_level = TemporalArtifactRedactionLevel.RESTRICTED
            await session.commit()
    adapter = SimpleNamespace(start_workflow=AsyncMock())
    async with artifacts() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        monkeypatch.setattr(service, "_enforce_worker_code_freshness", AsyncMock())
        with pytest.raises(HTTPException) as denied:
            await executions.create_execution(
                payload={
                    "type": "workflow",
                    "payload": {
                        **roots,
                        "workflow": {
                            "instructions": "Inspect the repository",
                            "publish": {"mode": "none"},
                        },
                    },
                },
                service=service,
                session=session,
                user=OperatorAdmission(via="loopback"),
                _submit_enabled=None,
                principal_context={},
                authorization=None,
                execution_fanout=None,
            )
        assert denied.value.status_code == 422
        assert denied.value.detail["code"] == "invalid_execution_request"
        expected = (
            "planDigest"
            if corruption == "binding"
            else (
                "SAVED_WORK_QUARANTINED"
                if protection == "quarantined"
                else "cannot access restricted raw artifact"
            )
        )
        assert expected in denied.value.detail["message"]
        adapter.start_workflow.assert_not_awaited()
        records = (
            (await session.execute(select(TemporalExecutionCanonicalRecord)))
            .scalars()
            .all()
        )
        assert records == []
