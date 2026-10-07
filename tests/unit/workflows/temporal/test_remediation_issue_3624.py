"""Capability-truthful remediation catalog coverage for GitHub issue #3624."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from api_service.services.remediation_actions import (
    TemporalRemediationControlPlane,
    build_remediation_action_executor,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationCapabilityContext,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_action_capability,
    remediation_action_capability_matrix,
    remediation_action_kinds,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationTargetHealthSnapshot,
)

# Use the real SDK adapter with an injected hermetic client. The suite's live
# Temporal guard replaces lifecycle methods after collection.
_UPDATE_WORKFLOW = TemporalClientAdapter.update_workflow


def _target():
    return RemediationTargetHealthSnapshot(
        workflow_id="target",
        pinned_run_id="source-run",
        current_run_id="source-run",
        state="executing",
        close_status=None,
        title=None,
        summary=None,
        target_run_changed=False,
        runtime="omnigent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,graceful", [("execution.cancel", True), ("execution.force_terminate", False)]
)
async def test_execution_stop_uses_existing_cleanup_and_result_owner(kind, graceful):
    owner = AsyncMock()
    client = AsyncMock()
    result = await TemporalRemediationControlPlane(
        client=client, execution_service=owner
    ).handlers()[kind](
        {
            "actionKind": kind,
            "actionId": "stop-1",
            "params": {"reason": "operator repair"},
        },
        {},
        _target(),
    )
    owner.cancel_execution.assert_awaited_once_with(
        workflow_id="target",
        reason="operator repair",
        graceful=graceful,
        expected_run_id="source-run",
    )
    client.cancel_workflow.assert_not_awaited()
    client.terminate_workflow.assert_not_awaited()
    assert result["status"] == "accepted"
    assert result["verificationRequired"] is True


@pytest.mark.asyncio
async def test_changed_run_cannot_be_authorized_by_overriding_expected_run():
    owner = AsyncMock()
    result = await TemporalRemediationControlPlane(execution_service=owner).handlers()[
        "execution.pause"
    ](
        {
            "actionKind": "execution.pause",
            "actionId": "pause-1",
            "params": {"expectedRunId": "sibling-run"},
        },
        {},
        replace(_target(), current_run_id="sibling-run", target_run_changed=True),
    )
    assert result["status"] == "precondition_failed"
    owner.signal_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_update_retry_keeps_exact_run_and_temporal_update_identity():
    class Handle:
        effects = 0
        receipts = set()

        async def execute_update(self, name, *, id):
            if id in self.receipts:
                return None
            self.receipts.add(id)
            self.effects += 1
            raise TimeoutError("acknowledgment lost after acceptance")

    handle = Handle()
    sdk = SimpleNamespace(get_workflow_handle=MagicMock(return_value=handle))
    client = TemporalClientAdapter(client=sdk)
    with pytest.raises(TimeoutError):
        await _UPDATE_WORKFLOW(
            client, "target", "Pause", run_id="source-run", idempotency_key="pause-1"
        )
    await _UPDATE_WORKFLOW(
        client, "target", "Pause", run_id="source-run", idempotency_key="pause-1"
    )
    assert handle.effects == 1
    assert sdk.get_workflow_handle.call_args_list == [
        call("target", run_id="source-run"),
        call("target", run_id="source-run"),
    ]


@pytest.mark.asyncio
async def test_rerun_adapter_hands_off_exact_result_identity():
    owner = AsyncMock()
    owner.create_fresh_rerun_execution.return_value = {
        "accepted": True,
        "workflow_id": "repair",
        "run_id": "repair-run",
    }
    result = await TemporalRemediationControlPlane(execution_service=owner).handlers()[
        "execution.start_fresh_rerun"
    ](
        {
            "actionKind": "execution.start_fresh_rerun",
            "actionId": "rerun-1",
            "params": {},
        },
        {},
        _target(),
    )
    assert result["resultingIdentity"] == {
        "workflowId": "repair",
        "runId": "repair-run",
    }


def test_capability_matrix_has_one_complete_row_per_catalog_action() -> None:
    matrix = remediation_action_capability_matrix()
    assert len(matrix) == len({row["actionKind"] for row in matrix})
    for row in matrix:
        assert set(
            (
                "requestable",
                "dryRunSupported",
                "executionBackendReady",
                "approvalBackendReady",
                "verificationBackendReady",
                "supportedTargetRuntimes",
                "supportedHostModes",
                "requiredEvidenceClasses",
                "blockedReasons",
            )
        ).issubset(row)
        assert row["requestable"] is (not row["blockedReasons"])


def test_every_requestable_action_has_execution_and_verification_owners() -> None:
    for action_kind in remediation_action_kinds():
        capability = remediation_action_capability(action_kind)
        assert capability["executionBackendReady"] is True
        assert capability["approvalBackendReady"] is True
        assert capability["verificationBackendReady"] is True
        assert capability["dryRunSupported"] is False


def test_incomplete_owners_are_disabled_with_bounded_reasons() -> None:
    expected = {
        "session.terminate": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "session.restart_container": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "session.clear": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "cleanup.request_janitor": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "cleanup.verify": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "target.annotate": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "target.verify": {
            "execution_backend_unavailable",
            "authoritative_verifier_unavailable",
        },
        "host.restart": {"authoritative_verifier_unavailable"},
        "workload.restart_helper_container": {
            "authoritative_verifier_unavailable"
        },
    }
    for action_kind, reasons in expected.items():
        capability = remediation_action_capability(action_kind)
        assert capability["requestable"] is False
        assert set(capability["blockedReasons"]) == reasons


def test_production_executor_registers_only_requestable_actions() -> None:
    executor = build_remediation_action_executor()
    assert set(executor._adapters) == set(remediation_action_kinds()) - {
        "checkpoint_branch.create_from_remediation_context"
    }
    assert (
        "checkpoint_branch.create_from_remediation_context"
        not in executor._adapters
    )
    assert "session.terminate" not in executor._adapters
    assert "cleanup.request_janitor" not in executor._adapters
    assert "host.restart" not in executor._adapters


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        (
            RemediationCapabilityContext(target_state_eligible=False),
            "target_state_ineligible",
        ),
        (
            RemediationCapabilityContext(approval_backend_ready=False),
            "approval_backend_unavailable",
        ),
        (
            RemediationCapabilityContext(
                execution_backend_readiness={"execution.pause": False}
            ),
            "execution_backend_unavailable",
        ),
        (
            RemediationCapabilityContext(
                verification_backend_readiness={"execution.pause": False}
            ),
            "authoritative_verifier_unavailable",
        ),
        (
            RemediationCapabilityContext(target_runtime="unknown-runtime"),
            "target_runtime_unsupported",
        ),
        (
            RemediationCapabilityContext(policy_allowed_action_kinds=()),
            "target_policy_denied",
        ),
        (
            RemediationCapabilityContext(caller_allowed_action_kinds=()),
            "caller_permission_denied",
        ),
    ],
)
def test_live_readiness_transitions_are_bounded(context, reason) -> None:
    capability = remediation_action_capability("execution.pause", context=context)
    assert capability["requestable"] is False
    assert reason in capability["blockedReasons"]


@pytest.mark.parametrize(
    "action_kind",
    [
        "session.interrupt_turn",
        "provider_profile.evict_stale_lease",
        "workload.restart_helper_container",
        "host.drain",
        "host_lease.reconcile_stale",
    ],
)
def test_action_specific_runtime_and_host_filtering(action_kind: str) -> None:
    capability = remediation_action_capability(
        action_kind,
        context=RemediationCapabilityContext(
            target_runtime="codex_cli", host_mode="external"
        ),
    )
    assert capability["supportedTargetRuntimes"] == ["omnigent"]
    assert capability["supportedHostModes"] == [
        "static_compose",
        "on_demand_docker",
    ]
    assert set(capability["blockedReasons"]) >= {
        "target_runtime_unsupported",
        "host_mode_unsupported",
    }


def test_allowed_actions_are_derived_from_live_evaluated_rows() -> None:
    service = RemediationActionAuthorityService(session=None)  # type: ignore[arg-type]
    permissions = RemediationPermissionSet(
        can_view_target=True,
        can_request_admin_profile=True,
    )
    profile = RemediationSecurityProfile(
        profile_ref="profile-1",
        execution_principal="service:remediation",
        allowed_action_kinds=("execution.pause", "execution.resume"),
    )
    rows = service.list_allowed_actions(
        permissions=permissions,
        security_profile=profile,
        capability_context=RemediationCapabilityContext(
            policy_allowed_action_kinds=("execution.pause",),
            target_runtime="temporal",
            host_mode="managed",
        ),
    )
    assert [row["actionKind"] for row in rows] == ["execution.pause"]


@pytest.mark.parametrize("action_kind", ["session.interrupt_turn", "session.cancel"])
def test_required_canonical_session_controls_have_a_result_verifier(action_kind):
    from moonmind.workflows.temporal.remediation_verification import (
        verification_contract_for,
    )

    contract = verification_contract_for(action_kind)
    assert contract.automatically_verifiable
    assert contract.evidence_owner == "canonical_session_turn_command"


@pytest.mark.asyncio
async def test_same_workflow_rerun_adapter_pins_admitted_run():
    owner = AsyncMock()
    owner.update_execution.return_value = {"accepted": True, "run_id": "result-run"}
    result = await TemporalRemediationControlPlane(execution_service=owner).handlers()[
        "execution.request_rerun_same_workflow"
    ](
        {
            "actionKind": "execution.request_rerun_same_workflow",
            "actionId": "rerun-1",
            "params": {},
        },
        {},
        _target(),
    )
    assert result["status"] == "accepted"
    owner.update_execution.assert_awaited_once_with(
        workflow_id="target",
        update_name="RequestRerun",
        idempotency_key="rerun-1",
        expected_run_id="source-run",
    )


@pytest.mark.asyncio
async def test_resume_audit_reason_is_not_an_agent_message():
    owner = AsyncMock()
    result = await TemporalRemediationControlPlane(execution_service=owner).handlers()[
        "execution.resume"
    ](
        {
            "actionKind": "execution.resume",
            "actionId": "resume-1",
            "params": {"reason": "administrative audit reason"},
        },
        {},
        _target(),
    )
    assert result["status"] == "accepted"
    assert owner.signal_execution.await_args.kwargs["payload"] is None
