"""Persisted default Codex plans grant native review without launching an agent."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from temporalio.testing import ActivityEnvironment

from api_service.db import models
from api_service.services import omnigent_execution_plan_service as compiler
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.workflows.temporal.activities import omnigent_session_activities as reader
from moonmind.workflows.temporal.merge_automation_repository_access import (
    acquire_merge_automation_repository_credential,
)
from tests.integration.reliability.test_repository_access_consumers_4676 import (
    _REPOSITORY,
    _WORKFLOW,
    _ready_opencode_image_pair,  # noqa: F401
    _target,
    repository_consumers as _repository_consumers,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _policy_snapshot,
    _snapshot,
)

repository_consumers = _repository_consumers
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


async def _compile_native_review_plan(context, monkeypatch, tmp_path):
    from api_service.api.routers.executions import (
        _build_original_workflow_input_snapshot_payload,
        _snapshot_source_payload_from_parameters,
    )
    from tests.unit.api.test_pr_review_resolve_preset import _expand

    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED", "false")
    monkeypatch.delenv("MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK", raising=False)
    monkeypatch.setenv(
        "OMNIGENT_SHARED_HOST_IMAGE_REF",
        "ghcr.io/example/omnigent-host@sha256:" + "f" * 64,
    )

    async def resolve_policy(**_kwargs):
        return _policy_snapshot(harness="codex-native", policy="codex-on-demand@1")

    monkeypatch.setattr(compiler, "_resolve_runtime_policy_snapshot", resolve_policy)
    expanded = await _expand(
        tmp_path,
        {"pull_request": "350", "review_only": True},
        context={"repository": _REPOSITORY},
    )
    parameters = {
        "model": "example/model",
        "targetRuntime": "omnigent",
        "repository": _target(),
        "publishMode": "none",
        "requiredCapabilities": ["git", "gh"],
        "workflow": expanded,
    }
    source, workflow = _snapshot_source_payload_from_parameters(parameters)
    snapshot = _build_original_workflow_input_snapshot_payload(
        source_kind="create", payload=source, task_payload=workflow
    )
    snapshot_ref, digest = await compiler.persist_json_artifact(
        artifact_service=context.artifacts,
        principal="user-1",
        artifact_class="original_task_input_snapshot",
        payload=snapshot,
    )
    compiled = await compiler.compile_and_persist_execution_plan(
        session_factory=context.sessions,
        execution_plan_store=context.plans,
        artifact_service=context.artifacts,
        principal="user-1",
        workflow_id=_WORKFLOW,
        agent_profile_snapshot=_snapshot(
            harness="codex-native", policy="codex-on-demand@1", provider_id="codex"
        ),
        provider_profile=SimpleNamespace(
            profile_id="codex", runtime_id="codex_cli", provider_id="openai"
        ),
        initial_parameters=parameters,
        authored_request_ref=snapshot_ref,
        authored_request_digest=digest,
        task_input_snapshot_ref=snapshot_ref,
        task_input_snapshot_digest=digest,
    )
    return compiled, parameters


async def test_default_codex_persisted_native_plan_acquires_only_review_authority(
    repository_consumers, monkeypatch, tmp_path
):
    context = repository_consumers
    compiled, _parameters = await _compile_native_review_plan(
        context, monkeypatch, tmp_path
    )
    plan = compiled.envelope.payload
    assert plan.executionRealizerRef == "codex-profile-bound@1"
    assert plan.credentialBindings["collaboration"].consumer == "native"
    assert set(plan.resolvedTools["repositoryAccess"]) == {"collaboration"}
    assert await context.plans.load(compiled.envelope.planRef) == compiled.envelope
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=_WORKFLOW, workflow_run_id="native-run"
    )
    authority = {
        "executionOwner": _WORKFLOW,
        "parentExecutionPlan": compiled.binding.model_dump(by_alias=True),
    }
    for operation in ("read", "review_request"):
        acquired = await environment.run(
            acquire_merge_automation_repository_credential,
            authority,
            repository=_REPOSITORY,
            operation=operation,
        )
        try:
            assert acquired.binding.connection_id == "selected-repository"
            assert acquired.binding.execution_owner == _WORKFLOW
            assert set(acquired.binding.operations) == {"read", "review_request"}
            assert acquired.credential.use_now(bytes) == b"selected-credential-canary"
        finally:
            acquired.credential.clear()

    with pytest.raises(HarnessPlatformError, match="native-only.*agent"):
        await reader.omnigent_evaluate_session_admission_activity(
            {
                "agentRunId": "forbidden-agent",
                "workflowId": _WORKFLOW,
                "stepExecutionId": f"{_WORKFLOW}:native-run:review:execution:1",
                "executionProfileRef": "codex",
                "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
            }
        )
    with pytest.raises(ValueError, match="target conflicts"):
        await environment.run(
            acquire_merge_automation_repository_credential,
            authority,
            repository="Other/Repository",
            operation="review_request",
        )

    async with context.sessions() as session:
        connection = await session.get(
            models.RepositoryConnectionRecord, "selected-repository"
        )
        connection.lifecycle = "disabled"
        await session.commit()
    with pytest.raises(ValueError, match="unavailable|disabled|active|lifecycle"):
        await environment.run(
            acquire_merge_automation_repository_credential,
            authority,
            repository=_REPOSITORY,
            operation="review_request",
        )


@pytest.mark.parametrize(
    "change",
    [
        None,
        "prefixed_ref",
        "double_prefixed_ref",
        "mapping_ref",
        "tool",
        "selector",
        "extra_step",
        "dynamic_policy",
        "finish_mode",
    ],
)
async def test_native_authority_validates_effective_executable_plan(
    repository_consumers, monkeypatch, tmp_path, change
):
    import copy
    import json
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalActivityRuntimeError,
        TemporalPlanActivities,
    )
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    context = repository_consumers
    compiled, parameters = await _compile_native_review_plan(
        context, monkeypatch, tmp_path
    )
    activities = TemporalPlanActivities(
        artifact_service=context.artifacts, planner=_build_runtime_planner()
    )
    generated = await activities.plan_generate(
        principal="user-1", parameters=parameters
    )
    ref = generated.plan_ref.artifact_id
    _artifact, body = await context.artifacts.read(artifact_id=ref, principal="user-1")
    payload = json.loads(body)
    if change == "tool":
        payload["nodes"][0]["tool"]["name"] = "github.update_issue_status"
    elif change == "selector":
        payload["nodes"][0]["inputs"]["pullRequest"] = "999"
    elif change == "extra_step":
        extra = copy.deepcopy(payload["nodes"][0])
        extra["id"] = "unexpected-extra"
        payload["nodes"].append(extra)
    elif change == "dynamic_policy":
        payload["policy"]["max_concurrency"] = 2
    elif change == "finish_mode":
        parameters["workflow"]["publish"]["mergeAutomation"]["finishMode"] = "merge"
    mismatch = change not in {
        None,
        "prefixed_ref",
        "double_prefixed_ref",
        "mapping_ref",
    }
    if mismatch:
        ref, _digest = await compiler.persist_json_artifact(
            artifact_service=context.artifacts,
            principal="user-1",
            artifact_class="workflow.plan",
            payload=payload,
        )
    elif change == "prefixed_ref":
        ref = "artifact:" + ref
    elif change == "double_prefixed_ref":
        ref = "artifact://" + ref
    elif change == "mapping_ref":
        from dataclasses import asdict

        ref = asdict(generated.plan_ref)
    environment = ActivityEnvironment()
    environment.info = replace(
        environment.info, workflow_id=_WORKFLOW, workflow_run_id="native-run"
    )
    kwargs = dict(
        plan_ref=ref,
        principal="user-1",
        omnigent_execution_plan=compiled.binding.model_dump(by_alias=True),
        execution_parameters=parameters,
    )
    if mismatch:
        with pytest.raises(
            (ValueError, TemporalActivityRuntimeError),
            match="native.*plan|native.*graph",
        ):
            await environment.run(activities.plan_validate, **kwargs)
    else:
        assert await environment.run(activities.plan_validate, **kwargs) == ref


async def test_agent_binding_keeps_existing_effective_plan_behavior(
    repository_consumers,
):
    from moonmind.workflows.temporal.activity_runtime import TemporalPlanActivities

    context = repository_consumers
    compiled = await context.compile(profile_tools=("gh",))
    activities = TemporalPlanActivities(artifact_service=context.artifacts)
    assert (
        await activities.plan_validate(
            plan_ref="ordinary-plan-is-read-by-the-existing-executor",
            principal="user-1",
            omnigent_execution_plan=compiled.binding.model_dump(by_alias=True),
            execution_parameters={"workflow": {"instructions": "Ordinary agent work."}},
        )
        == "ordinary-plan-is-read-by-the-existing-executor"
    )
