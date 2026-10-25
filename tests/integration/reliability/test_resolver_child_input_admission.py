"""Temporal-started resolvers admit inputs before their first Skill read."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceResponse,
    DescribeWorkflowExecutionResponse,
)
from temporalio.testing import ActivityEnvironment

from api_service.db import models
from moonmind.omnigent import bridge_artifacts
from moonmind.omnigent.bridge_artifacts import OmnigentArtifactError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import omnigent_activities as children
from moonmind.workflows.temporal.activity_runtime import (
    ArtifactParentAuthorityUnavailable,
    TemporalAgentRuntimeActivities,
    TemporalPlanActivities,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from tests.integration.reliability.test_repository_access_consumers_4676 import (  # noqa: F401
    _WORKFLOW,
    _ready_opencode_image_pair,
    _target,
)
from tests.integration.reliability.test_repository_access_consumers_4676 import (
    repository_consumers as _repository_consumers,
)
from tests.unit.omnigent.test_admitted_input_authority import _protect

repository_consumers = _repository_consumers

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_CHILD = "resolver:pr:350:head:candidate:1"


@pytest_asyncio.fixture
async def resolver_parent(repository_consumers):
    context = repository_consumers
    parent = await context.compile(
        "pr", profile_tools=("gh",), mergeAutomation={"enabled": True}
    )
    await context.link_inputs(parent)
    binding = parent.binding.model_dump(by_alias=True)
    async with context.sessions() as session:
        session.add(
            models.TemporalExecutionCanonicalRecord(
                workflow_id=_WORKFLOW,
                run_id="parent-run",
                owner_id="system",
                workflow_type=models.TemporalWorkflowType.USER_WORKFLOW,
                entry="run",
                parameters={"omnigentExecutionPlan": binding},
            )
        )
        session.add(
            models.ManagedAgentProviderProfile(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            )
        )
        await session.commit()
    described = DescribeWorkflowExecutionResponse(
        workflow_execution_info={
            "execution": {"workflow_id": "merge-gate", "run_id": "gate-run"},
            "parent_execution": {"workflow_id": _WORKFLOW, "run_id": "parent-run"},
            "parent_namespace_id": "namespace-default",
        }
    )
    server = SimpleNamespace(
        describe_workflow_execution=AsyncMock(return_value=described),
        describe_namespace=AsyncMock(
            return_value=DescribeNamespaceResponse(
                namespace_info={"id": "namespace-default"},
            )
        ),
    )
    environment = ActivityEnvironment(
        client=SimpleNamespace(namespace="default", workflow_service=server)
    )
    environment.info = replace(
        environment.info, workflow_id="merge-gate", workflow_run_id="gate-run"
    )
    payload = {
        "principal": "system",
        "parentWorkflowId": _WORKFLOW,
        "parentExecutionPlan": binding,
        "childWorkflowId": _CHILD,
        "childWorkflowRequest": {
            "initial_parameters": {
                "targetRuntime": "omnigent",
                "repository": _target(),
                "publishMode": "auto",
                "workflow": {
                    "instructions": "Resolve the selected pull request.",
                    "tool": {"type": "skill", "name": "pr-resolver"},
                    "inputs": {"pr": "350", "finishMode": "fix_only"},
                },
            }
        },
    }
    return SimpleNamespace(
        context=context,
        parent=parent,
        environment=environment,
        server=server,
        record=described.workflow_execution_info,
        payload=payload,
    )


@pytest.mark.parametrize("binding_source", ["carried", "canonical"])
async def test_system_resolver_preparation_reuses_verified_parent_grant(
    resolver_parent,
    binding_source,
):
    state = resolver_parent
    context = state.context
    if binding_source == "canonical":
        state.payload.pop("parentExecutionPlan")
    parent_ref = state.parent.binding.plan_artifact_ref
    async with context.sessions() as session:
        repository = TemporalArtifactRepository(session)
        original = await repository.get_artifact(parent_ref)
        original_identity = (original.sha256, original.created_by_principal)
        original_links = {link.id for link in await repository.list_links(parent_ref)}
        # The failure requires machine provenance and a different artifact owner.
        with pytest.raises(TemporalArtifactAuthorizationError):
            await TemporalArtifactService(repository).read(
                artifact_id=parent_ref,
                principal="service:omnigent_session_supervisor",
                admitted_principal="system",
            )

    for _ in range(2):
        prepared = await state.environment.run(
            TemporalAgentRuntimeActivities().omnigent_prepare_child_execution_plan,
            state.payload,
        )
        parameters = prepared["initial_parameters"]
        assert (
            parameters["omnigentExecutionPlan"]["planRef"]
            != state.parent.binding.plan_ref
        )
        async with context.sessions() as session:
            repository = TemporalArtifactRepository(session)
            child = await repository.get_artifact(
                parameters["omnigentExecutionPlan"]["planArtifactRef"]
            )
            assert child.created_by_principal == "system"
            retained = await repository.get_artifact(parent_ref)
            assert (retained.sha256, retained.created_by_principal) == original_identity
            assert {
                link.id for link in await repository.list_links(parent_ref)
            } == original_links

    # The resolver's actual run still admits its own fresh Skill closure.
    child_environment = ActivityEnvironment()
    child_environment.info = replace(
        child_environment.info, workflow_id=_CHILD, workflow_run_id="actual-child-run"
    )
    await child_environment.run(
        TemporalPlanActivities(artifact_service=context.artifacts).plan_validate,
        plan_ref="validated-agent-plan",
        principal=f"workflow:{_CHILD}",
        omnigent_execution_plan=parameters["omnigentExecutionPlan"],
        execution_parameters=parameters,
    )
    async with context.sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        assert (
            await service.read(
                artifact_id=parameters["resolvedSkillsetRef"],
                principal=f"workflow:{_CHILD}",
            )
        )[1]
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=parent_ref, principal=f"workflow:{_CHILD}")


@pytest.mark.parametrize(
    "fault",
    [
        "parent",
        "activity-run",
        "namespace",
        "no-parent",
        "missing-parent",
        "unlinked-plan",
        "unavailable",
    ],
)
async def test_resolver_preparation_rejects_unverified_parent_before_writing(
    resolver_parent,
    monkeypatch,
    fault,
):
    state = resolver_parent
    error_match = "parent"
    if fault == "parent":
        state.payload["parentWorkflowId"] = "unrelated-parent"
        state.payload["principal"] = "operator"
    elif fault == "activity-run":
        state.record.execution.run_id = "another-gate-run"
    elif fault == "namespace":
        state.record.parent_namespace_id = "another-namespace"
    elif fault == "no-parent":
        state.record.ClearField("parent_execution")
    elif fault == "missing-parent":
        state.payload.pop("parentWorkflowId")
    elif fault == "unlinked-plan":
        unrelated = await state.context.compile()
        state.payload["parentExecutionPlan"] = unrelated.binding.model_dump(
            by_alias=True
        )
        error_match = "cannot read artifact"
    else:
        state.server.describe_workflow_execution.side_effect = TimeoutError(
            "Temporal unavailable"
        )
    from api_service.services import omnigent_execution_plan_service as writer

    write = AsyncMock(wraps=writer.persist_json_artifact)
    monkeypatch.setattr(writer, "persist_json_artifact", write)
    expected = (
        ArtifactParentAuthorityUnavailable
        if fault == "unavailable"
        else TemporalArtifactAuthorizationError
    )
    with pytest.raises(expected, match=error_match):
        await state.environment.run(
            TemporalAgentRuntimeActivities().omnigent_prepare_child_execution_plan,
            state.payload,
        )
    write.assert_not_awaited()


async def _prepare_child(context):
    parent = await context.compile(
        "pr", profile_tools=("gh",), mergeAutomation={"enabled": True}
    )
    async with context.sessions() as session:
        session.add(
            models.ManagedAgentProviderProfile(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            )
        )
        await session.commit()
    return await children.omnigent_prepare_child_execution_plan_activity(
        {
            "principal": "user-1",
            "parentWorkflowId": _WORKFLOW,
            "parentExecutionPlan": parent.binding.model_dump(by_alias=True),
            "childWorkflowId": _CHILD,
            "childWorkflowRequest": {
                "initial_parameters": {
                    "targetRuntime": "omnigent",
                    "repository": _target(),
                    "publishMode": "auto",
                    "workflow": {
                        "instructions": "Resolve the selected pull request.",
                        "tool": {"type": "skill", "name": "pr-resolver"},
                        "inputs": {"pr": "350", "finishMode": "fix_only"},
                    },
                }
            },
        }
    )


@pytest.mark.parametrize("namespace", ["default", "another-namespace"])
async def test_compiled_child_reaches_first_skill_read_and_survives_worker_retry(
    repository_consumers, namespace
):
    context = repository_consumers
    prepared = await _prepare_child(context)
    parameters = prepared["initial_parameters"]
    manifest_ref = parameters["resolvedSkillsetRef"]
    principal = f"workflow:{_CHILD}"
    # The parent preparation Activity has no child Temporal run ID. Its
    # artifacts deliberately start unlinked, exactly like the failed run.
    async with context.sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(artifact_id=manifest_ref, principal=principal)

    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info,
        namespace=namespace,
        workflow_id=_CHILD,
        workflow_run_id="actual-child-run",
    )
    plans = TemporalPlanActivities(
        artifact_service=context.artifacts, planner=_build_runtime_planner()
    )
    generated = await environment.run(
        plans.plan_generate, principal=principal, parameters=parameters
    )
    validation = dict(
        plan_ref=generated.plan_ref,
        principal=principal,
        omnigent_execution_plan=parameters["omnigentExecutionPlan"],
        execution_parameters=parameters,
    )
    # Exercise the Activity the workflow already calls immediately before
    # reading its Skill manifest; no AgentRun or API create path runs first.
    assert (
        await environment.run(plans.plan_validate, **validation) == generated.plan_ref
    )
    async with context.sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        original, body = await service.read(
            artifact_id=manifest_ref, principal=principal
        )
        manifest = json.loads(body)
        bundle_ref = next(
            skill["content_ref"]
            for skill in manifest["skills"]
            if skill["skill_name"] == "pr-resolver"
        )
        original_digest = original.sha256
        original_owner = original.created_by_principal
        assert original_owner == "user-1"
        for unrelated in ("workflow:unrelated", f"workflow:{_WORKFLOW}"):
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(artifact_id=manifest_ref, principal=unrelated)

    # A replacement worker reconstructs its services and retries admission.
    for _ in range(2):
        replacement = TemporalPlanActivities(artifact_service=context.artifacts)
        await environment.run(replacement.plan_validate, **validation)
    async with context.sessions() as session:
        links = (
            await session.scalars(
                select(models.TemporalArtifactLink).where(
                    models.TemporalArtifactLink.workflow_id == _CHILD
                )
            )
        ).all()
        assert links
        assert {link.namespace for link in links} == {namespace}
        assert {link.run_id for link in links} == {"actual-child-run"}
        assert len(links) == len({link.artifact_id for link in links})
        retained, _ = await TemporalArtifactService(
            TemporalArtifactRepository(session)
        ).read(artifact_id=manifest_ref, principal=principal)
        assert retained.sha256 == original_digest
        assert retained.created_by_principal == original_owner

    # The subsequent runtime reader consumes the same admitted bundle, not
    # only a mocked compiler result or an operator-visible metadata record.
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId=_CHILD,
        idempotencyKey="resolver-step",
        omnigentExecutionPlan=parameters["omnigentExecutionPlan"],
        stepExecution={
            "workflowId": _CHILD,
            "runId": "actual-child-run",
            "logicalStepId": "node-1",
            "executionOrdinal": 1,
            "stepExecutionId": f"{_CHILD}:actual-child-run:node-1:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    assert await context.gateway.read_admitted_input(bundle_ref, request=request)


@pytest.mark.parametrize(
    "fault", ["binding-digest", "plan-pointer", "restricted", "quarantined"]
)
async def test_child_validation_refuses_invalid_inputs_before_skill_dispatch(
    repository_consumers, fault
):
    context = repository_consumers
    parameters = (await _prepare_child(context))["initial_parameters"]
    binding = parameters["omnigentExecutionPlan"]
    if fault == "binding-digest":
        binding["planDigest"] = "sha256:" + "f" * 64
    elif fault == "plan-pointer":
        binding["planArtifactRef"] = parameters["resolvedSkillsetRef"]
    else:
        await _protect(context.sessions, parameters["resolvedSkillsetRef"], fault)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=_CHILD, workflow_run_id="actual-child-run"
    )
    plans = TemporalPlanActivities(artifact_service=context.artifacts)
    with pytest.raises(
        (
            ValueError,
            OmnigentArtifactError,
            TemporalArtifactAuthorizationError,
            TemporalArtifactStateError,
        ),
        match={
            "binding-digest": "planDigest must match planRef",
            "plan-pointer": "plan artifact pointer conflicts",
            "restricted": "restricted raw artifact",
            "quarantined": "SAVED_WORK_QUARANTINED",
        }[fault],
    ):
        await environment.run(
            plans.plan_validate,
            plan_ref="not-consumed-before-admission",
            principal=f"workflow:{_CHILD}",
            omnigent_execution_plan=binding,
            execution_parameters=parameters,
        )
    async with context.sessions() as session:
        assert not (
            await session.scalars(
                select(models.TemporalArtifactLink).where(
                    models.TemporalArtifactLink.workflow_id == _CHILD
                )
            )
        ).all()


async def test_child_validation_recovers_interrupted_grants_without_duplicating_them(
    repository_consumers, monkeypatch
):
    context = repository_consumers
    parameters = (await _prepare_child(context))["initial_parameters"]
    link = bridge_artifacts._link_execution_input
    calls = 0

    async def interrupted_link(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TemporalArtifactStateError("link persistence unavailable")
        await link(*args, **kwargs)

    monkeypatch.setattr(bridge_artifacts, "_link_execution_input", interrupted_link)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=_CHILD, workflow_run_id="actual-child-run"
    )
    validation = dict(
        plan_ref="validated-agent-plan",
        principal=f"workflow:{_CHILD}",
        omnigent_execution_plan=parameters["omnigentExecutionPlan"],
        execution_parameters=parameters,
    )
    with pytest.raises(
        TemporalArtifactStateError, match="link persistence unavailable"
    ):
        await environment.run(
            TemporalPlanActivities(artifact_service=context.artifacts).plan_validate,
            **validation,
        )
    for _ in range(2):
        assert (
            await environment.run(
                TemporalPlanActivities(
                    artifact_service=context.artifacts
                ).plan_validate,
                **validation,
            )
            == "validated-agent-plan"
        )
    async with context.sessions() as session:
        links = (
            await session.scalars(
                select(models.TemporalArtifactLink).where(
                    models.TemporalArtifactLink.workflow_id == _CHILD
                )
            )
        ).all()
        assert len(links) == len({link.artifact_id for link in links})
        assert (
            await TemporalArtifactService(TemporalArtifactRepository(session)).read(
                artifact_id=parameters["resolvedSkillsetRef"],
                principal=f"workflow:{_CHILD}",
            )
        )[1]
