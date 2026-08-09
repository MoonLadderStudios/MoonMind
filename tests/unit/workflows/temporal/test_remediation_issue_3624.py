"""Capability-truthful remediation catalog coverage for GitHub issue #3624."""

from moonmind.workflows.temporal.remediation_actions import (
    remediation_action_capability,
    remediation_action_capability_matrix,
    remediation_action_kinds,
)
from api_service.services.remediation_actions import (
    build_remediation_action_executor,
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
    assert set(executor._adapters) == set(remediation_action_kinds())
    assert "session.terminate" not in executor._adapters
    assert "cleanup.request_janitor" not in executor._adapters
    assert "host.restart" not in executor._adapters
