from copy import deepcopy

import pytest
from pydantic import ValidationError

from moonmind.omnigent.policies import (
    PolicyDocument,
    compile_policy_snapshot,
    document_digest,
    resolve_action,
)


def policy_document() -> dict:
    return {
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded"]},
        "execution": {"profileRef": "omnigent-codex@1", "harness": "codex-native", "agentIdentities": ["codex"]},
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
