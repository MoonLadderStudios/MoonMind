from copy import deepcopy

import pytest
from pydantic import ValidationError

from moonmind.omnigent.policies import (
    PolicyDocument,
    bind_approval_request,
    compile_policy_snapshot,
    document_digest,
    policy_authority_evidence,
    resolve_action,
    validate_approval_binding,
    validate_policy_authority_evidence,
)
from api_service.services.omnigent_policies import bootstrap_document, validate_policy


def policy_document() -> dict:
    return {
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded"]},
        "execution": {"profileRef": "omnigent-codex@1", "harness": "codex-native", "agentIdentities": ["codex-native-ui"]},
        "host": {"mode": "static_compose", "backendRef": "compose", "architectures": ["amd64"],
                 "serverImageRef": "images/omnigent@sha256:" + "1" * 64,
                 "hostImageRef": "images/host@sha256:" + "2" * 64},
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "processes": 256,
                      "timeoutSeconds": 5400, "temporaryStorageMiB": 256, "concurrency": 1},
        "network": {"attachmentRef": "local-network", "egressProfileRef": "egress-default"},
        "workspace": {"allowedClasses": ["workflow"], "repositoryMutation": True,
                      "mountClasses": ["workspace", "oauth_home"], "runtimeUid": 1000, "runtimeGid": 1000},
        "providerProfile": {"compatibleProviders": ["codex"], "queueWhenBusy": True},
        "session": {"create": True, "firstMessage": "required", "continuation": True,
                    "interruption": True, "cancellation": True, "cleanup": "drain"},
        "capture": {"required": True, "artifactClasses": ["events", "snapshot"], "maxLogBytes": 1000000, "redaction": "required"},
        "checkpoint": {"capture": True, "resume": True, "branch": True, "publication": "approval", "promotion": "verified"},
        "remediation": {"actions": ["retry"], "riskTiers": {"retry": "low"}, "locks": True, "maxActions": 3, "autonomous": False},
        "rag": {"initialScope": "workflow", "followupScope": "session", "collectionRefs": ["default"],
                "tokenBudget": 4000, "fallback": "deny", "credentialRef": "retrieval-default"},
        "approvals": {"actions": {
            "read": {"decision": "allow", "reason": "read-only"},
            "publish": {"decision": "approval_required", "approvalClass": "release", "reviewerRule": "owner", "reason": "publication"},
        }},
        "retention": {"days": 30, "deletion": "after-expiry"},
        "rollout": {"cohort": "default", "gate": "ready", "diagnostics": True},
    }


def test_digest_and_compilation_are_reproducible_and_cover_every_boundary():
    document = policy_document()
    first = compile_policy_snapshot(policy_id="codex-static", version=2, document=document, validation={"valid": True})
    second = compile_policy_snapshot(policy_id="codex-static", version=2, document=deepcopy(document), validation={"valid": True})
    assert first == second
    assert first["policyDigest"] == document_digest(document)
    assert set(first["boundaries"]) == {
        "schemaVersion", "endpoint", "execution", "host", "resources", "network",
        "workspace", "providerProfile", "session", "capture", "checkpoint",
        "remediation", "rag", "approvals", "retention", "rollout",
    }


def test_policy_rejects_secret_bodies_raw_paths_and_docker_socket():
    for key, value in (
        ("secretBody", "not-safe"),
        ("credentialBody", "not-safe"),
        ("mount", "/host/private"),
        ("socket", "unix:///var/run/docker.sock"),
    ):
        document = policy_document()
        document["workspace"][key] = value
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(document)


def test_actions_resolve_deterministically_and_fail_closed():
    snapshot = compile_policy_snapshot(policy_id="p", version=1, document=policy_document(), validation={"valid": True})
    assert resolve_action(snapshot, "read")["decision"] == "allow"
    assert resolve_action(snapshot, "publish") == {
        "decision": "approval_required", "reason": "publication",
        "approvalClass": "release", "reviewerRule": "owner",
    }
    assert resolve_action(snapshot, "unknown")["decision"] == "deny"


def test_policy_authority_evidence_is_compact_and_rejects_stale_records():
    snapshot = compile_policy_snapshot(
        policy_id="p", version=1, document=policy_document(), validation={"valid": True}
    )
    evidence = policy_authority_evidence(snapshot)
    assert set(evidence) == {
        "policyId",
        "policyVersion",
        "policyRef",
        "policyDigest",
        "snapshotRef",
        "validation",
    }
    validate_policy_authority_evidence(evidence, snapshot)
    evidence["policyDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="policy authority evidence mismatch"):
        validate_policy_authority_evidence(evidence, snapshot)


def test_snapshot_identity_is_stable_across_revalidation_timestamps():
    document = policy_document()
    first = compile_policy_snapshot(
        policy_id="p", version=1, document=document,
        validation={"valid": True, "diagnostics": [], "validatedAt": "2026-01-01T00:00:00Z"},
    )
    # A later re-validation of the unchanged document rewrites only mutable
    # validation metadata; the immutable snapshot identity must not move.
    second = compile_policy_snapshot(
        policy_id="p", version=1, document=document,
        validation={"valid": True, "diagnostics": [], "validatedAt": "2026-09-09T09:09:09Z"},
    )
    assert first["snapshotRef"] == second["snapshotRef"]
    assert first["policyDigest"] == second["policyDigest"]
    # Evidence pinned at the first launch still validates against the later
    # snapshot even though validatedAt changed.
    launched_evidence = policy_authority_evidence(first)
    validate_policy_authority_evidence(launched_evidence, second)
    # The full launch snapshot (carrying the original validation metadata) also
    # validates against the re-validated snapshot.
    validate_policy_authority_evidence(dict(first), second)


def test_production_checkpoint_evidence_is_cold_restore_eligible():
    # The production checkpoint capture stamps the six compact fields from the
    # compiled launch snapshot (see profile_bound_execution). Reproduce that
    # mapping and confirm the resulting evidence passes the exact policy check
    # that validate_restore_material performs, keeping the checkpoint cold-
    # restore eligible against its run snapshot.
    snapshot = compile_policy_snapshot(
        policy_id="omnigent-codex", version=1, document=policy_document(),
        validation={"valid": True, "diagnostics": [], "validatedAt": "2026-01-01T00:00:00Z"},
    )
    checkpoint_evidence = {
        "policyId": snapshot["policyId"],
        "policyVersion": snapshot["policyVersion"],
        "policyRef": snapshot["policyRef"],
        "policyDigest": snapshot["policyDigest"],
        "snapshotRef": snapshot["snapshotRef"],
        "validation": snapshot["validation"],
    }
    validate_policy_authority_evidence(checkpoint_evidence, snapshot)

    # A legacy checkpoint that carries no policy evidence fails closed rather
    # than silently cold-restoring on a less-constrained path.
    legacy_evidence = {key: None for key in checkpoint_evidence}
    with pytest.raises(ValueError):
        validate_policy_authority_evidence(legacy_evidence, snapshot)


def _bootstrap_snapshot():
    document = bootstrap_document(
        host_mode="static_compose", execution_profile_ref="omnigent-codex@1"
    )
    return compile_policy_snapshot(
        policy_id="omnigent-codex", version=1, document=document,
        validation={"valid": True},
    )


def test_bootstrap_policy_authorizes_every_remediation_action_identity():
    # Imported lazily so the module-level import graph stays free of the
    # temporal package cycle; every production remediation adapter must have a
    # non-deny rule under the default bootstrap policy.
    from moonmind.workflows.temporal.remediation_actions import (
        remediation_action_kinds,
    )

    snapshot = _bootstrap_snapshot()
    for kind in remediation_action_kinds():
        assert resolve_action(snapshot, kind)["decision"] != "deny", kind


def test_bootstrap_keeps_mutating_remediation_actions_approval_gated():
    from moonmind.workflows.temporal.remediation_actions import (
        remediation_action_kinds,
    )

    snapshot = _bootstrap_snapshot()
    evidence_only = {"cleanup.verify", "target.annotate", "target.verify"}
    for kind in remediation_action_kinds():
        decision = resolve_action(snapshot, kind)
        if kind in evidence_only:
            assert decision["decision"] == "allow", kind
        else:
            # State-mutating actions stay approval-gated (never silently allow)
            # and carry a complete, bindable approval rule.
            assert decision["decision"] == "approval_required", kind
            assert decision["approvalClass"] and decision["reviewerRule"]


def test_bootstrap_does_not_authorize_unready_action():
    snapshot = _bootstrap_snapshot()
    assert resolve_action(snapshot, "host.restart")["decision"] == "deny"


def test_policy_authority_evidence_rejects_unvalidated_snapshot():
    snapshot = compile_policy_snapshot(
        policy_id="p", version=1, document=policy_document(), validation={"valid": False}
    )
    with pytest.raises(ValueError, match="not validated"):
        policy_authority_evidence(snapshot)


def test_policy_sections_are_typed_and_cross_field_combinations_fail_closed():
    document = policy_document()
    document["session"]["cleanup"] = "remove"
    with pytest.raises(ValidationError, match="static_compose requires"):
        PolicyDocument.model_validate(document)

    document = policy_document()
    document["remediation"]["autonomous"] = True
    document["remediation"]["locks"] = False
    with pytest.raises(ValidationError, match="autonomous remediation requires locks"):
        PolicyDocument.model_validate(document)

    document = policy_document()
    document["workspace"]["unrecognizedAuthority"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PolicyDocument.model_validate(document)


def test_activation_validation_checks_deployment_capabilities_and_digest_images():
    document = policy_document()
    document["host"]["serverImageRef"] = "image-ref:mutable"
    validation, compatibility = validate_policy(
        PolicyDocument.model_validate(document),
        capabilities={
            "hostModes": {"on_demand_docker"},
            "backends": {"container-backend"},
            "architectures": {"amd64"},
            "providers": {"other"},
            "workspaceClasses": {"scratch"},
        },
    )
    assert validation["valid"] is False
    assert compatibility["compatible"] is False
    assert set(compatibility["diagnosticCodes"]) >= {
        "OMNIGENT_INVALID_IMAGE_REF",
        "OMNIGENT_HOST_MODE_UNAVAILABLE",
        "OMNIGENT_BACKEND_UNAVAILABLE",
        "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE",
        "OMNIGENT_WORKSPACE_CLASS_UNSUPPORTED",
    }

    placeholder = policy_document()
    placeholder["host"]["serverImageRef"] = "images/omnigent@sha256:" + "0" * 64
    validation, _ = validate_policy(
        PolicyDocument.model_validate(placeholder),
        capabilities={
            "hostModes": {"static_compose"},
            "backends": {"compose"},
            "architectures": {"amd64"},
            "providers": {"codex"},
            "workspaceClasses": {"workflow"},
        },
    )
    assert validation["valid"] is False


def test_approval_required_rule_must_be_complete_at_document_validation():
    document = policy_document()
    del document["approvals"]["actions"]["publish"]["reviewerRule"]
    with pytest.raises(ValidationError, match="approval_required needs"):
        PolicyDocument.model_validate(document)


def test_approval_binding_carries_exact_policy_authority_and_expected_state():
    snapshot = compile_policy_snapshot(
        policy_id="p", version=3, document=policy_document(), validation={"valid": True}
    )
    binding = bind_approval_request(
        snapshot, "publish", target_expected_state="ready"
    )
    assert binding == {
        "policyRef": "p@3",
        "policyDigest": snapshot["policyDigest"],
        "snapshotRef": snapshot["snapshotRef"],
        "targetExpectedState": "ready",
        "approvalClass": "release",
        "reviewerRule": "owner",
    }
    validate_approval_binding(binding, snapshot, target_current_state="ready")


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("policyRef", "p@4"),
        ("policyDigest", "sha256:stale"),
        ("snapshotRef", "omnigent-policy:sha256:stale"),
        ("targetExpectedState", "changed"),
    ),
)
def test_approval_binding_rejects_stale_policy_or_target_state(field, replacement):
    snapshot = compile_policy_snapshot(
        policy_id="p", version=3, document=policy_document(), validation={"valid": True}
    )
    binding = bind_approval_request(
        snapshot, "publish", target_expected_state="ready"
    )
    binding[field] = replacement
    with pytest.raises(ValueError, match="stale approval binding"):
        validate_approval_binding(binding, snapshot, target_current_state="ready")
