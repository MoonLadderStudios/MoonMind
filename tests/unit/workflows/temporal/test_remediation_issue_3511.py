"""Bounded remaining contract coverage for MoonLadderStudios/MoonMind#3511."""

from __future__ import annotations

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
