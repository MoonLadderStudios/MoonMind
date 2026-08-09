"""Bounded remaining contract coverage for MoonLadderStudios/MoonMind#3511."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio

from api_service.services.remediation_actions import TemporalRemediationControlPlane
from moonmind.omnigent.policies import bind_approval_request
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_changes_require_checkpoint_branch,
)
from moonmind.workflows.temporal.remediation_tools import (
    MoonMindControlPlaneRemediationActionExecutor,
    RemediationEvidenceToolService,
    RemediationEvidenceToolError,
    RemediationTargetHealthSnapshot,
    _approval_binding_from_state,
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


def test_unready_control_plane_actions_are_not_advertised() -> None:
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

    assert catalog == ()


async def test_issue_3620_authority_persists_and_resolves_exact_expiring_approval() -> None:
    link = SimpleNamespace(
        remediation_workflow_id="remediation-1",
        remediation_run_id="remediation-run-1",
        target_workflow_id="target-1",
        target_run_id="target-run-1",
        authority_mode="approval_gated",
        approval_state=None,
    )
    session = AsyncMock()
    session.get.side_effect = lambda model, _identity: (
        link
        if model.__name__ == "TemporalExecutionRemediationLink"
        else SimpleNamespace(state=SimpleNamespace(value="failed"))
    )
    session.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    )
    publisher = AsyncMock()
    publisher.publish_json_artifact.return_value = SimpleNamespace(
        artifact_id="artifact-approval-request"
    )
    service = RemediationActionAuthorityService(
        session=session, lifecycle_publisher=publisher
    )
    kwargs = dict(
        remediation_workflow_id="remediation-1",
        action_kind="host.restart",
        parameters={
            "providerProfileId": "profile-1",
            "hostLeaseRef": "lease-1",
            "expectedHostState": "running",
        },
        dry_run=False,
        idempotency_key="action-1",
        requesting_principal="operator:requester",
        permissions=RemediationPermissionSet(
            can_view_target=True, can_request_admin_profile=True
        ),
        security_profile=RemediationSecurityProfile(
            profile_ref="admin",
            execution_principal="operator:requester",
            allowed_action_kinds=("host.restart",),
        ),
    )

    pending = await service.evaluate_action_request(**kwargs)
    assert pending.decision == "approval_required"
    assert link.approval_state["status"] == "pending"
    assert link.approval_state["requestDigest"]
    assert link.approval_state["expectedTargetState"] == "failed"
    assert link.approval_state["parameterDigest"]
    assert link.approval_state["artifactRefs"] == {
        "approvalRequest": "artifact-approval-request"
    }
    assert publisher.publish_json_artifact.await_args.kwargs["artifact_type"] == (
        "remediation.approval_request"
    )

    # A new service instance models worker restart / Workflow replay. The
    # persisted request is reused and publication is deduplicated by its stable
    # artifact label instead of creating another logical request.
    restarted = RemediationActionAuthorityService(
        session=session, lifecycle_publisher=publisher
    )
    replayed = await restarted.evaluate_action_request(**kwargs)
    assert replayed.decision == "approval_required"
    assert link.approval_state["requestId"] == (
        "remediation-1:approval:" + link.approval_state["requestDigest"][:24]
    )
    assert publisher.publish_json_artifact.await_count == 2

    link.approval_state.update(
        status="approved",
        decisionActor="operator:reviewer",
        expiresAt=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    allowed = await restarted.evaluate_action_request(
        **kwargs, approval_ref=link.approval_state["approvalRef"]
    )
    assert allowed.decision == "allowed"

    denied = await service.evaluate_action_request(
        **kwargs, approval_ref="approval://remediation/caller-invented"
    )
    assert denied.decision == "denied"
    assert denied.reason == "approval_not_found"


def test_issue_3620_approval_rejects_each_stale_authority_dimension() -> None:
    link = SimpleNamespace(
        target_run_id="run-1",
        approval_state={
            "approvalRef": "approval://remediation/1",
            "status": "approved",
            "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "actionKind": "host.restart",
            "requestDigest": "request-digest",
            "parameterDigest": "parameter-digest",
            "targetRunId": "run-1",
            "expectedTargetState": "failed",
            "checkpointRef": "artifact://checkpoint-1",
            "stepExecutionId": "step-1",
            "bridgeSessionId": "bridge-1",
            "omnigentSessionId": "session-1",
            "hostRef": "host-1",
            "hostLeaseRef": "lease-1",
            "providerProfileLeaseRef": "slot-1",
            "credentialGeneration": 4,
            "policyRef": "policy-1@7",
            "policyDigest": "sha256:policy",
            "policySnapshotRef": "omnigent-policy:sha256:snapshot",
            "securityProfileRef": "security-1",
        },
    )
    current = {
        "targetState": "failed",
        "checkpointRef": "artifact://checkpoint-1",
        "stepExecutionId": "step-1",
        "bridgeSessionId": "bridge-1",
        "omnigentSessionId": "session-1",
        "hostRef": "host-1",
        "hostLeaseRef": "lease-1",
        "providerProfileLeaseRef": "slot-1",
        "credentialGeneration": 4,
        "policyRef": "policy-1@7",
        "policyDigest": "sha256:policy",
        "policySnapshotRef": "omnigent-policy:sha256:snapshot",
        "securityProfileRef": "security-1",
    }

    validate = RemediationActionAuthorityService._validate_persisted_approval
    common = dict(
        link=link,
        approval_ref="approval://remediation/1",
        action_kind="host.restart",
        request_shape_hash="request-digest",
        parameter_digest="parameter-digest",
    )
    assert validate(**common, current_authority=current) is None
    expected = {
        "targetState": "approval_stale_target_state",
        "checkpointRef": "approval_stale_checkpoint",
        "stepExecutionId": "approval_stale_checkpoint",
        "bridgeSessionId": "approval_stale_bridge_session",
        "omnigentSessionId": "approval_stale_session",
        "hostRef": "approval_stale_host",
        "hostLeaseRef": "approval_stale_host_lease",
        "providerProfileLeaseRef": "approval_stale_provider_profile_lease",
        "credentialGeneration": "approval_stale_credential_generation",
        "policyRef": "approval_stale_policy",
        "policyDigest": "approval_stale_policy",
        "policySnapshotRef": "approval_stale_policy",
        "securityProfileRef": "approval_stale_security_profile",
    }
    for field, reason in expected.items():
        stale = {**current, field: "changed"}
        assert validate(**common, current_authority=stale) == reason

    assert (
        validate(
            **{**common, "parameter_digest": "changed"},
            current_authority=current,
        )
        == "approval_parameter_mismatch"
    )


def _policy_snapshot(decision: str) -> dict:
    rule = {"decision": decision, "reason": f"test-{decision}"}
    if decision == "approval_required":
        rule.update(approvalClass="operations", reviewerRule="workflow-owner")
    return {
        "policyId": "policy-1",
        "policyVersion": 7,
        "policyRef": "policy-1@7",
        "policyDigest": "sha256:" + "a" * 64,
        "snapshotRef": "omnigent-policy:sha256:" + "b" * 64,
        "validation": {"valid": True},
        "boundaries": {"approvals": {"actions": {"host.restart": rule}}},
    }


async def test_production_policy_boundary_allows_and_stamps_exact_authority() -> None:
    called = False

    async def adapter(*_args):
        nonlocal called
        called = True
        return {"status": "accepted"}

    wrapped = TemporalRemediationControlPlane._policy_bound(adapter)
    result = await wrapped(
        {"actionKind": "host.restart", "policySnapshot": _policy_snapshot("allow")},
        {},
        _production_target_health(),
    )

    assert called is True
    assert result["status"] == "accepted"
    assert set(result["policyAuthority"]) == {
        "policyId", "policyVersion", "policyRef", "policyDigest",
        "snapshotRef", "validation",
    }


@pytest.mark.parametrize("snapshot", [None, _policy_snapshot("deny")])
async def test_production_policy_boundary_denies_missing_or_denied_authority(snapshot) -> None:
    called = False

    async def adapter(*_args):
        nonlocal called
        called = True
        return {"status": "accepted"}

    request = {"actionKind": "host.restart"}
    if snapshot is not None:
        request["policySnapshot"] = snapshot
    result = await TemporalRemediationControlPlane._policy_bound(adapter)(
        request, {}, _production_target_health()
    )

    assert called is False
    assert result["status"] == "denied"


async def test_production_policy_boundary_binds_then_revalidates_approval() -> None:
    snapshot = _policy_snapshot("approval_required")
    called = False

    async def adapter(*_args):
        nonlocal called
        called = True
        return {"status": "accepted"}

    wrapped = TemporalRemediationControlPlane._policy_bound(adapter)
    pending = await wrapped(
        {"actionKind": "host.restart", "policySnapshot": snapshot},
        {},
        _production_target_health(),
    )
    assert pending["status"] == "approval_required"
    assert pending["approvalBinding"]["targetExpectedState"] == "target-run"
    assert called is False

    approved = await wrapped(
        {
            "actionKind": "host.restart",
            "policySnapshot": snapshot,
            "approvalBinding": pending["approvalBinding"],
            "approvalRef": "approval://operations/1",
        },
        {},
        _production_target_health(),
    )
    assert approved["status"] == "accepted"
    assert called is True


async def test_production_policy_boundary_rejects_stale_policy_and_target_bindings() -> None:
    snapshot = _policy_snapshot("approval_required")
    binding = bind_approval_request(
        snapshot, "host.restart", target_expected_state="old-run"
    )

    async def adapter(*_args):
        raise AssertionError("stale approval must fail before side effects")

    result = await TemporalRemediationControlPlane._policy_bound(adapter)(
        {
            "actionKind": "host.restart",
            "policySnapshot": snapshot,
            "approvalBinding": binding,
            "approvalRef": "approval://operations/1",
        },
        {},
        _production_target_health(),
    )
    assert result["status"] == "denied"
    assert result["reason"] == "omnigent_approval_binding_stale"
    assert "targetExpectedState" in result["detail"]


async def test_production_policy_boundary_rejects_unapproved_binding() -> None:
    snapshot = _policy_snapshot("approval_required")
    binding = bind_approval_request(
        snapshot, "host.restart", target_expected_state="target-run"
    )

    async def adapter(*_args):
        raise AssertionError("an unapproved binding must fail before side effects")

    result = await TemporalRemediationControlPlane._policy_bound(adapter)(
        {
            "actionKind": "host.restart",
            "policySnapshot": snapshot,
            "approvalBinding": binding,
        },
        {},
        _production_target_health(),
    )
    assert result["status"] == "denied"
    assert result["reason"] == "omnigent_approval_reference_required"


def _bridge_session(session_execute_result) -> AsyncMock:
    """Wire an async session whose ``execute`` returns a sync SQLAlchemy result.

    ``await AsyncMock().execute(...)`` returns an AsyncMock, so calling the sync
    ``scalar_one_or_none()`` on it would yield an un-awaited coroutine. The real
    ``AsyncSession.execute`` resolves to a synchronous ``Result``; model that by
    pinning ``execute.return_value`` to a MagicMock.
    """

    session = AsyncMock()
    session.execute.return_value = session_execute_result
    return session


async def test_real_handoff_resolves_persisted_target_run_policy() -> None:
    snapshot = _policy_snapshot("allow")
    bridge = SimpleNamespace(
        effective_launch_snapshot_json={"policyAuthority": snapshot}
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = bridge
    service = object.__new__(RemediationEvidenceToolService)
    service._session = _bridge_session(result)

    policy_service = AsyncMock()
    policy_service.resolve_runtime_snapshot.return_value = snapshot
    with patch(
        "api_service.services.omnigent_policies.OmnigentPolicyService",
        return_value=policy_service,
    ):
        resolved = await service._resolve_target_policy_snapshot(
            target=_production_target_health()
        )

    assert resolved == snapshot
    policy_service.resolve_runtime_snapshot.assert_awaited_once_with("policy-1@7")


async def test_real_handoff_rejects_stale_persisted_policy_before_dispatch() -> None:
    launch_snapshot = _policy_snapshot("allow")
    current_snapshot = {**launch_snapshot, "policyDigest": "sha256:" + "c" * 64}
    bridge = SimpleNamespace(
        effective_launch_snapshot_json={"policyAuthority": launch_snapshot}
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = bridge
    service = object.__new__(RemediationEvidenceToolService)
    service._session = _bridge_session(result)

    policy_service = AsyncMock()
    policy_service.resolve_runtime_snapshot.return_value = current_snapshot
    with patch(
        "api_service.services.omnigent_policies.OmnigentPolicyService",
        return_value=policy_service,
    ), pytest.raises(RemediationEvidenceToolError, match="stale or unavailable"):
        await service._resolve_target_policy_snapshot(
            target=_production_target_health()
        )


async def test_resolve_returns_none_for_non_omnigent_target() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None  # no Omnigent bridge session
    service = object.__new__(RemediationEvidenceToolService)
    service._session = _bridge_session(result)

    target = RemediationTargetHealthSnapshot(
        workflow_id="target",
        pinned_run_id="run",
        current_run_id="run",
        state="failed",
        close_status="failed",
        title=None,
        summary=None,
        target_run_changed=False,
        runtime="codex_cli",
    )
    assert await service._resolve_target_policy_snapshot(target=target) is None


async def test_resolve_fails_closed_for_declared_omnigent_without_bridge() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    service = object.__new__(RemediationEvidenceToolService)
    service._session = _bridge_session(result)

    target = RemediationTargetHealthSnapshot(
        workflow_id="target",
        pinned_run_id="run",
        current_run_id="run",
        state="failed",
        close_status="failed",
        title=None,
        summary=None,
        target_run_changed=False,
        runtime="omnigent",
    )
    with pytest.raises(
        RemediationEvidenceToolError, match="immutable Omnigent policy authority"
    ):
        await service._resolve_target_policy_snapshot(target=target)


async def test_policy_bound_allows_verified_non_omnigent_target() -> None:
    called = False

    async def adapter(*_args):
        nonlocal called
        called = True
        return {"status": "accepted"}

    result = await TemporalRemediationControlPlane._policy_bound(adapter)(
        {"actionKind": "execution.pause", "targetRuntime": "codex_cli"},
        {},
        _production_target_health(),
    )
    assert called is True
    assert result["status"] == "accepted"


async def test_policy_bound_denies_omnigent_target_missing_snapshot() -> None:
    async def adapter(*_args):
        raise AssertionError("must not dispatch without an Omnigent snapshot")

    result = await TemporalRemediationControlPlane._policy_bound(adapter)(
        {"actionKind": "host.restart", "targetRuntime": "omnigent"},
        {},
        _production_target_health(),
    )
    assert result["status"] == "denied"
    assert result["reason"] == "omnigent_policy_snapshot_required"


def test_issue_3620_dispatch_binding_comes_only_from_persisted_approval() -> None:
    assert _approval_binding_from_state(
        {
            "policyRef": "policy-1@7",
            "policyDigest": "sha256:policy",
            "policySnapshotRef": "omnigent-policy:sha256:snapshot",
            "expectedTargetState": "failed",
            "approvalClass": "operations",
            "reviewerRule": "workflow-owner",
            "callerBinding": "must-not-propagate",
        }
    ) == {
        "policyRef": "policy-1@7",
        "policyDigest": "sha256:policy",
        "snapshotRef": "omnigent-policy:sha256:snapshot",
        "targetExpectedState": "failed",
        "approvalClass": "operations",
        "reviewerRule": "workflow-owner",
    }


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

    assert result["status"] == "accepted"
    assert result["deliveryStage"] == "branch_graph_created"
    assert result["branchTurnLaunched"] is False
    assert result["terminalBranchResultAvailable"] is False
    assert result["idempotencyIdentity"] == "action-branch"
    assert result["verificationContract"]["automaticallyVerifiable"] is True
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

    result = await plane.handlers()["workload.reap_orphan_container"](
        {
            "actionKind": "workload.reap_orphan_container",
            "actionId": "action-4",
            "params": {"containerRef": "container-1"},
        },
        {},
        _production_target_health(),
    )

    assert result["status"] == "delivery_unknown"
    assert result["afterEvidenceRefs"] == []
    assert "RuntimeError" in result["reason"]
