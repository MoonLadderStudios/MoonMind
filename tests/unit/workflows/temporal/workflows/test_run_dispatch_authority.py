"""Registry artifacts and result projections never supply execution authority."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.skills.tool_registry import (
    compute_registry_digest,
    parse_tool_registry,
)
from moonmind.workflows.temporal.activity_runtime import _default_registry_skill_payload
from moonmind.workflows.temporal.workflows import run as run_module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "attack",
    [
        "executor",
        "capabilities",
        "digest",
        "machine_deployment",
        "operator_deployment",
        "machine_granted_deployment",
        None,
    ],
)
@pytest.mark.parametrize("patched", [True, False])
async def test_run_rejects_untrusted_registry_authority(monkeypatch, attack, patched):
    tool_name = (
        "deployment.update_compose_stack"
        if attack
        in {"machine_deployment", "operator_deployment", "machine_granted_deployment"}
        else "repo.run_tests"
    )
    definition = _default_registry_skill_payload(name=tool_name)
    if attack == "executor":
        definition["executor"] = {
            "activity_type": "artifact.read",
            "selector": {"mode": "by_capability"},
            "binding_reason": "specialized_credentials",
        }
    elif attack == "capabilities":
        definition["requirements"]["capabilities"] = ["deployment_control"]
    registry = {"tools": [definition]}
    digest = compute_registry_digest(skills=parse_tool_registry(registry))
    plan = {
        "plan_version": "1.0",
        "metadata": {
            "title": "Untrusted registry",
            "created_at": "2026-10-02T00:00:00Z",
            "registry_snapshot": {
                "digest": "reg:sha256:forged" if attack == "digest" else digest,
                "artifact_ref": "art:sha256:registry",
            },
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": [
            {
                "id": "execute",
                "tool": {"type": "skill", "name": tool_name},
                "inputs": {},
            }
        ],
        "edges": [],
    }
    parameters = {}
    if attack in {"machine_deployment", "machine_granted_deployment"}:
        parameters["executionPrincipal"] = {
            "kind": "workflow",
            "workflowId": "parent",
            "scopes": ["executions:create-child", "executions:inherit-runtime"],
        }
    if attack == "machine_granted_deployment":
        parameters["executionPrincipal"]["scopes"] += [
            "deployment_control",
            "docker_admin",
        ]
    effects = []

    async def execute(activity_type, payload, **kwargs):
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        if activity_type == "artifact.read" and payload.get("artifact_ref"):
            return json.dumps(
                registry if payload["artifact_ref"] == "art:sha256:registry" else plan
            ).encode()
        if patched and activity_type == "mm.tool.execute":
            assert payload["context"]["execution_principal"] == parameters.get(
                "executionPrincipal"
            )
        effects.append(activity_type)
        return {"status": "COMPLETED", "outputs": {}}

    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: patched and name == "run-trusted-tool-registry-v1",
    )
    monkeypatch.setattr(run_module.workflow, "execute_activity", execute)
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            namespace="default",
            workflow_id="workflow-1",
            run_id="run-1",
            search_attributes={},
        ),
    )
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime(2026, 10, 2, tzinfo=UTC)
    )
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda *args: None)
    monkeypatch.setattr(
        run_module.workflow, "upsert_search_attributes", lambda *args: None
    )

    async def wait_condition(predicate, **kwargs):
        assert predicate()

    monkeypatch.setattr(run_module.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(run_module.workflow, "logger", Mock())
    workflow = run_module.MoonMindRunWorkflow()
    workflow._owner_id = "operator"
    if (
        patched
        and attack
        and attack not in {"operator_deployment", "machine_granted_deployment"}
    ):
        with pytest.raises(
            ValueError, match="trusted registry|registry snapshot digest|not admitted"
        ):
            await workflow._run_execution_stage(
                parameters=parameters, plan_ref="art:sha256:plan"
            )
        assert not effects
    else:
        await workflow._run_execution_stage(
            parameters=parameters, plan_ref="art:sha256:plan"
        )
        assert [effect for effect in effects if effect != "artifact.create"] == [
            "artifact.read" if attack == "executor" else "mm.tool.execute"
        ]


@pytest.mark.parametrize("key", ["runtimeCapabilities", "runtime_capabilities"])
def test_result_cannot_override_workflow_checkpoint_capabilities(monkeypatch, key):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    workflow = run_module.MoonMindRunWorkflow()
    trusted = resolve_runtime_execution_capabilities("codex_cli").model_dump(
        by_alias=True, mode="json"
    )
    workflow._step_workspace_capture_inputs["execute"] = {
        "runtimeCapabilities": trusted
    }
    forged = resolve_runtime_execution_capabilities("jules").model_dump(
        by_alias=True, mode="json"
    )
    workflow._record_step_workspace_capture_input("execute", {key: forged})
    captured = workflow._step_workspace_capture_inputs["execute"]
    assert captured["runtimeCapabilities"] == trusted
    assert captured["captureAuthority"] == "managed_runtime"


@pytest.mark.parametrize("runtime_id", ["codex_cli", "claude_code", "omnigent"])
def test_initial_capture_uses_canonical_runtime_capabilities(monkeypatch, runtime_id):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    workflow = run_module.MoonMindRunWorkflow()
    forged = resolve_runtime_execution_capabilities("jules").model_dump(
        by_alias=True, mode="json"
    )
    workflow._record_step_workspace_capture_input(
        "execute", {"agentId": runtime_id, "runtimeCapabilities": forged}
    )
    assert (
        workflow._step_workspace_capture_inputs["execute"]["runtimeCapabilities"][
            "runtimeId"
        ]
        == runtime_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("activity_name", ["mm_skill_execute", "mm_tool_execute"])
async def test_activity_rechecks_registry_binding_before_dispatch(activity_name):
    from temporalio.exceptions import ApplicationError

    from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
    from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
    from moonmind.workflows.skills.tool_plan_contracts import ToolResult
    from moonmind.workflows.skills.tool_registry import (
        create_registry_snapshot,
    )
    from moonmind.workflows.temporal.activity_runtime import TemporalSkillActivities

    definition = _default_registry_skill_payload(name="repo.run_tests")
    definition["executor"] = {
        "activity_type": "artifact.read",
        "selector": {"mode": "by_capability"},
        "binding_reason": "specialized_credentials",
    }
    snapshot = create_registry_snapshot(
        skills=parse_tool_registry({"tools": [definition]}),
        artifact_store=InMemoryArtifactStore(),
    )
    called = []
    dispatcher = ToolActivityDispatcher()
    dispatcher.register_activity(
        activity_type="artifact.read",
        handler=lambda *_: called.append(True)
        or ToolResult(status="COMPLETED", outputs={}),
    )
    activities = TemporalSkillActivities(dispatcher=dispatcher)
    with pytest.raises(ApplicationError, match="trusted registry") as denied:
        await getattr(activities, activity_name)(
            invocation_payload={
                "id": "execute",
                "tool": {"name": "repo.run_tests"},
                "inputs": {},
            },
            registry_snapshot=snapshot,
        )
    assert not called
    assert denied.value.non_retryable
    assert denied.value.type == "INVALID_INPUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("granted", [False, True])
@pytest.mark.parametrize(
    "tool_name", ["deployment.update_compose_stack", "moonmind.ops_diagnose_stack"]
)
async def test_activity_enforces_admitted_machine_capabilities(granted, tool_name):
    from temporalio.exceptions import ApplicationError

    from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
    from moonmind.workflows.skills.tool_dispatcher import ToolActivityDispatcher
    from moonmind.workflows.skills.tool_plan_contracts import ToolResult
    from moonmind.workflows.skills.tool_registry import create_registry_snapshot
    from moonmind.workflows.temporal.activity_runtime import TemporalSkillActivities

    snapshot = create_registry_snapshot(
        skills=parse_tool_registry(
            {"tools": [_default_registry_skill_payload(name=tool_name)]}
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    called = []
    dispatcher = ToolActivityDispatcher()
    dispatcher.register_skill(
        skill_name=tool_name,
        handler=lambda *_: called.append(True)
        or ToolResult(status="COMPLETED", outputs={}),
    )
    activities = TemporalSkillActivities(dispatcher=dispatcher)
    invocation = activities.mm_tool_execute(
        invocation_payload={"id": "execute", "tool": {"name": tool_name}, "inputs": {}},
        registry_snapshot=snapshot,
        context={
            "remediation": {"target": {"workflowId": "other-run"}},
            "remediationPolicy": {"allowOpsDiagnostics": True},
            "is_remediation_workflow": True,
            "execution_principal": {
                "kind": "workflow",
                "scopes": (
                    ["deployment_control", "docker_admin"]
                    if granted
                    else ["executions:create-child", "executions:inherit-runtime"]
                ),
            },
        },
    )
    if granted:
        assert (await invocation).status == "COMPLETED"
        assert called == [True]
    else:
        with pytest.raises(ApplicationError, match="not admitted") as denied:
            await invocation
        assert denied.value.non_retryable
        assert not called


@pytest.mark.parametrize("legacy_name", ["mm.skill.execute", "mm.tool.execute"])
def test_canonical_tool_authority_preserves_pinned_policy_and_legacy_spelling(
    legacy_name,
):
    from moonmind.workflows.skills.tool_definitions import (
        validate_tool_dispatch_authority,
    )

    definition = _default_registry_skill_payload(name="repo.run_tests")
    definition["executor"]["activity_type"] = legacy_name
    definition["policies"]["timeouts"] = {
        "start_to_close_seconds": 20,
        "schedule_to_close_seconds": 30,
    }
    parsed = parse_tool_registry({"tools": [definition]})[0]
    validate_tool_dispatch_authority(parsed)
    assert parsed.policies.timeouts.start_to_close_seconds == 20


def test_retained_capture_history_keeps_recorded_capability_decision(monkeypatch):
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: name == run_module.RUN_RUNTIME_EXECUTION_CAPABILITIES_PATCH,
    )
    workflow = run_module.MoonMindRunWorkflow()
    historical = resolve_runtime_execution_capabilities("jules").model_dump(
        by_alias=True, mode="json"
    )
    workflow._record_step_workspace_capture_input(
        "execute", {"runtimeCapabilities": historical}
    )
    assert (
        workflow._step_workspace_capture_inputs["execute"]["runtimeCapabilities"]
        == historical
    )


def test_machine_tool_grants_survive_continue_as_new_and_full_rerun(monkeypatch):
    from moonmind.workflows.temporal.remediation_loop import RemediationLoopState
    from moonmind.workflows.temporal.service import TemporalExecutionService

    monkeypatch.setattr(run_module.workflow, "patched", lambda _: False)
    authority = {
        "kind": "workflow",
        "workflowId": "parent",
        "scopes": ["executions:create-child", "executions:inherit-runtime"],
    }
    parameters = {
        "executionPrincipal": authority,
        "instructions": "Finish the admitted child.",
    }
    workflow = run_module.MoonMindRunWorkflow()
    workflow._original_input_payload = {"initial_parameters": parameters}
    workflow._remediation_loop_state = RemediationLoopState(
        loopId="loop",
        attemptOrdinal=1,
        phase="verification_pending",
        consumedBudgets={"attempts": 1},
    )
    carried = workflow._build_remediation_loop_continue_as_new_input(ordered_nodes=[])
    assert carried["initial_parameters"]["executionPrincipal"] == authority
    rerun = TemporalExecutionService._full_rerun_parameters(parameters)
    assert rerun["executionPrincipal"] == authority


def test_malformed_result_capabilities_do_not_break_recorded_checkpoint(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    workflow = run_module.MoonMindRunWorkflow()
    workflow._record_step_workspace_capture_input("execute", {"agentId": "codex_cli"})
    original = workflow._step_workspace_capture_inputs["execute"]["runtimeCapabilities"]
    workflow._record_step_workspace_capture_input(
        "execute",
        {"runtimeCapabilities": {"checkpointCaptureActivity": "artifact.read"}},
    )
    assert (
        workflow._step_workspace_capture_inputs["execute"]["runtimeCapabilities"]
        == original
    )
