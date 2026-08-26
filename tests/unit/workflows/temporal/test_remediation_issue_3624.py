"""Capability-truthful remediation catalog coverage for GitHub issue #3624."""

import pytest

from api_service.services.remediation_actions import (
    build_remediation_action_executor,
)
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationCapabilityContext,
    RemediationPermissionSet,
    RemediationSecurityProfile,
    remediation_action_capability,
    remediation_action_capability_matrix,
    remediation_action_kinds,
)


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
