"""Bounded remaining contract coverage for MoonLadderStudios/MoonMind#3511."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api_service.services.remediation_actions import TemporalRemediationControlPlane
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_changes_require_checkpoint_branch,
)
from moonmind.workflows.temporal.remediation_tools import (
    MoonMindControlPlaneRemediationActionExecutor,
    RemediationTargetHealthSnapshot,
)


def test_corrected_execution_choices_require_checkpoint_branch() -> None:
    original = {
        "instructions": "Retry unchanged.",
        "runtime": "omnigent",
        "model": "gpt-5",
        "providerProfile": "profile-a",
        "launchPolicy": "standard",
        "repositoryBranch": "main",
        "workspacePolicy": "continue_from_previous_execution",
        "publishMode": "pr",
    }

    assert remediation_changes_require_checkpoint_branch(
        original=original,
        proposed=dict(original),
    ) == ()
    assert remediation_changes_require_checkpoint_branch(
        original=original,
        proposed={**original, "model": "gpt-5.1", "publishMode": "none"},
    ) == ("model", "publishMode")


def test_requested_control_plane_actions_are_in_typed_catalog() -> None:
    expected = {
        "host.drain",
        "host.stop",
        "host.restart",
        "host.remove",
        "host_lease.reconcile_stale",
        "cleanup.request_janitor",
        "cleanup.verify",
        "target.annotate",
        "target.verify",
    }
    service = RemediationActionAuthorityService(session=None)  # type: ignore[arg-type]
    catalog = service.list_allowed_actions(
        permissions=RemediationPermissionSet(
            can_view_target=True,
            can_request_admin_profile=True,
        ),
        security_profile=RemediationSecurityProfile(
            profile_ref="admin",
            execution_principal="service:test",
            allowed_action_kinds=tuple(expected),
        ),
    )

    assert expected == {item["actionKind"] for item in catalog}


async def test_control_plane_executor_dispatches_typed_adapter_with_evidence() -> None:
    calls: list[str] = []

    async def adapter(action_request, guard_result, target_health):
        calls.append(action_request["actionKind"])
        assert guard_result["executable"] is True
        assert target_health.workflow_id == "target"
        return {
            "status": "accepted",
            "beforeEvidenceRefs": ["artifact://before"],
            "afterEvidenceRefs": ["artifact://after"],
        }

    executor = MoonMindControlPlaneRemediationActionExecutor(
        {"host.restart": adapter}
    )
    result = await executor.execute_action(
        action_request={"actionKind": "host.restart"},
        guard_result={"executable": True},
        target_health=RemediationTargetHealthSnapshot(
            workflow_id="target",
            pinned_run_id="run",
            current_run_id="run",
            state="running",
            close_status=None,
            title=None,
            summary=None,
            target_run_changed=False,
        ),
    )

    assert calls == ["host.restart"]
    assert result["status"] == "accepted"
    assert result["beforeEvidenceRefs"] == ["artifact://before"]


async def test_control_plane_executor_denies_unwired_action() -> None:
    executor = MoonMindControlPlaneRemediationActionExecutor({})
    result = await executor.execute_action(
        action_request={"actionKind": "host.remove"},
        guard_result={"executable": True},
        target_health=RemediationTargetHealthSnapshot(
            workflow_id="target",
            pinned_run_id="run",
            current_run_id="run",
            state="running",
            close_status=None,
            title=None,
            summary=None,
            target_run_changed=False,
        ),
    )

    assert result["status"] == "denied"
    assert result["reason"] == "control_plane_adapter_unavailable"


async def test_control_plane_executor_requires_branch_for_corrected_input() -> None:
    executor = MoonMindControlPlaneRemediationActionExecutor({})
    result = await executor.execute_action(
        action_request={
            "actionKind": "execution.resume",
            "parameters": {"inputChanges": {"model": "gpt-5.1"}},
        },
        guard_result={"executable": True},
        target_health=RemediationTargetHealthSnapshot(
            workflow_id="target",
            pinned_run_id="run",
            current_run_id="run",
            state="running",
            close_status=None,
            title=None,
            summary=None,
            target_run_changed=False,
        ),
    )

    assert result["status"] == "denied"
    assert result["reason"] == "checkpoint_branch_required_for_corrected_input"
    assert result["changedFields"] == ["model"]


async def test_control_plane_executor_compares_authoritative_input_snapshots() -> None:
    called = False

    async def adapter(*_args):
        nonlocal called
        called = True
        return {"status": "accepted"}

    executor = MoonMindControlPlaneRemediationActionExecutor(
        {"execution.resume": adapter}
    )
    result = await executor.execute_action(
        action_request={
            "actionKind": "execution.resume",
            # A caller cannot hide an authoritative change with an empty hint.
            "parameters": {"inputChanges": {}},
        },
        guard_result={
            "executable": True,
            "originalInputs": {
                "runtime": "omnigent",
                "providerProfile": "profile-a",
                "publishMode": "pr",
            },
            "proposedInputs": {
                "runtime": "omnigent",
                "providerProfile": "profile-b",
                "publishMode": "none",
            },
        },
        target_health=RemediationTargetHealthSnapshot(
            workflow_id="target",
            pinned_run_id="run",
            current_run_id="run",
            state="running",
            close_status=None,
            title=None,
            summary=None,
            target_run_changed=False,
        ),
    )

    assert called is False
    assert result["status"] == "denied"
    assert result["changedFields"] == ["providerProfile", "publishMode"]


async def test_typed_evidence_operations_delegate_to_their_bounded_classes() -> None:
    service = object.__new__(RemediationEvidenceToolService)
    seen: list[str] = []

    async def read_evidence_page(**kwargs):
        seen.append(kwargs["evidence_class"])
        return kwargs["evidence_class"]

    service.read_evidence_page = read_evidence_page

    assert await service.read_execution_and_step_details(
        remediation_workflow_id="remediation"
    ) == "execution_and_steps"
    assert await service.read_checkpoint_and_recovery_manifests(
        remediation_workflow_id="remediation"
    ) == "checkpoint_and_recovery"
    assert await service.read_bridge_event_pages(
        remediation_workflow_id="remediation"
    ) == "bridge_events"
    assert await service.read_capture_and_resource_manifests(
        remediation_workflow_id="remediation"
    ) == "capture"
    assert await service.read_cleanup_and_janitor_evidence(
        remediation_workflow_id="remediation"
    ) == "lifecycle"
    assert await service.read_branch_and_publication_evidence(
        remediation_workflow_id="remediation"
    ) == "checkpoint_branches"
    assert await service.read_policy_and_approval_snapshots(
        remediation_workflow_id="remediation"
    ) == "policy"
    assert seen == [
        "execution_and_steps",
        "checkpoint_and_recovery",
        "bridge_events",
        "capture",
        "lifecycle",
        "checkpoint_branches",
        "policy",
    ]


def _production_target_health() -> RemediationTargetHealthSnapshot:
    return RemediationTargetHealthSnapshot(
        workflow_id="target",
        pinned_run_id="target-run",
        current_run_id="target-run",
        state="failed",
        close_status="failed",
        title=None,
        summary=None,
        target_run_changed=False,
    )


async def test_production_rerun_adapter_uses_execution_service_idempotently() -> None:
    client = AsyncMock()
    execution_service = AsyncMock()
    execution_service.create_fresh_rerun_execution.return_value = {
        "accepted": True,
        "message": "Fresh rerun created.",
        "workflow_id": "fresh-target",
    }
    plane = TemporalRemediationControlPlane(
        client=client, execution_service=execution_service
    )

    result = await plane.rerun(
        {
            "actionKind": "execution.start_fresh_rerun",
            "actionId": "action-1",
            "params": {"expectedRunId": "target-run"},
        },
        {},
        _production_target_health(),
    )

    assert result["status"] == "accepted"
    assert result["afterEvidenceRefs"] == [
        "execution:fresh-target:rerun-request:action-1"
    ]
    execution_service.create_fresh_rerun_execution.assert_awaited_once_with(
        workflow_id="target",
        idempotency_key="action-1",
    )


async def test_checkpoint_branch_adapter_persists_graph_through_service() -> None:
    checkpoint_service = AsyncMock()
    checkpoint_service.create_branch_graph.return_value = SimpleNamespace(
        branch=SimpleNamespace(branch_id="remediation-action-branch")
    )
    plane = TemporalRemediationControlPlane(
        client=AsyncMock(), checkpoint_branch_service=checkpoint_service
    )

    result = await plane.handlers()[
        "checkpoint_branch.create_from_remediation_context"
    ](
        {
            "actionKind": "checkpoint_branch.create_from_remediation_context",
            "actionId": "action-branch",
            "params": {
                "expectedRunId": "target-run",
                "remediationWorkflowId": "remediation",
                "remediationContextRef": "artifact://context",
                "checkpointRef": "artifact://checkpoint",
                "instructionRef": "artifact://instructions",
                "instructionDigest": "sha256:" + ("a" * 64),
            },
        },
        {},
        _production_target_health(),
    )

    assert result["status"] == "applied"
    payload = checkpoint_service.create_branch_graph.await_args.args[0]
    assert payload["source"]["workflowId"] == "target"
    assert payload["source"]["runId"] == "target-run"
    assert payload["source"]["checkpointRef"] == "artifact://checkpoint"
    assert payload["runtimeContextPolicy"] == "fresh_agent_run"
    assert payload["instructionRef"] == "artifact://instructions"
    assert payload["instructionDigest"] == "sha256:" + ("a" * 64)
    assert payload["idempotencyKey"] == "action-branch"


@pytest.mark.parametrize(
    ("kind", "params", "workflow_type", "workflow_id"),
    [
        (
            "host.restart",
            {
                "providerProfileId": "profile-1",
                "hostLeaseRef": "lease-1",
                "expectedHostState": "stopped",
            },
            "MoonMind.OmnigentOAuthHostJanitor",
            "remediation-omnigent:action-2",
        ),
        (
            "provider_profile.evict_stale_lease",
            {"providerProfileId": "profile-1", "hostLeaseRef": "lease-1"},
            "MoonMind.OmnigentOAuthHostJanitor",
            "remediation-omnigent:action-2",
        ),
        (
            "workload.reap_orphan_container",
            {"containerRef": "container-1", "expectedState": "orphaned"},
            "MoonMind.ManagedSessionReconcile",
            "remediation-managed-runtime:action-2",
        ),
        (
            "cleanup.request_janitor",
            {"cleanupRef": "cleanup-1", "expectedState": "pending"},
            "MoonMind.ManagedRuntimeWorkspaceCleanup",
            "remediation-cleanup:action-2",
        ),
    ],
)
async def test_production_repair_adapters_queue_owning_control_plane(
    kind, params, workflow_type, workflow_id
) -> None:
    client = AsyncMock()
    client.start_workflow.return_value = SimpleNamespace(
        workflow_id=workflow_id, run_id="control-run"
    )
    plane = TemporalRemediationControlPlane(client=client)

    result = await plane.handlers()[kind](
        {"actionKind": kind, "actionId": "action-2", "params": params},
        {},
        _production_target_health(),
    )

    assert result["status"] == "accepted"
    assert result["afterEvidenceRefs"] == [
        f"workflow:{workflow_id}:run:control-run"
    ]
    assert client.start_workflow.await_args.kwargs["workflow_type"] == workflow_type
    assert client.start_workflow.await_args.kwargs["workflow_id"] == workflow_id
    queued_payload = client.start_workflow.await_args.kwargs["input_args"]
    assert queued_payload["requestId"] == "action-2"
    if kind == "workload.reap_orphan_container":
        assert queued_payload["containerRef"] == "container-1"
        assert queued_payload["expectedState"] == "orphaned"
    if kind == "cleanup.request_janitor":
        assert queued_payload["actionKind"] == kind
        assert queued_payload["cleanupRef"] == "cleanup-1"


async def test_production_host_adapter_rejects_missing_authoritative_lease() -> None:
    plane = TemporalRemediationControlPlane(client=AsyncMock())

    result = await plane.handlers()["host.stop"](
        {
            "actionKind": "host.stop",
            "actionId": "action-3",
            "params": {"providerProfileId": "profile-1"},
        },
        {},
        _production_target_health(),
    )

    assert result["status"] == "precondition_failed"
    assert result["reason"] == "hostLeaseRef is required"


async def test_production_adapter_reports_delivery_unknown_without_claiming_success() -> None:
    client = AsyncMock()
    client.start_workflow.side_effect = RuntimeError("transport unavailable")
    plane = TemporalRemediationControlPlane(client=client)

    result = await plane.handlers()["cleanup.request_janitor"](
        {
            "actionKind": "cleanup.request_janitor",
            "actionId": "action-4",
            "params": {"cleanupRef": "cleanup-1"},
        },
        {},
        _production_target_health(),
    )

    assert result["status"] == "delivery_unknown"
    assert result["afterEvidenceRefs"] == []
    assert "RuntimeError" in result["reason"]
