"""Follow-up (in-session) retrieval authoring → launch snapshot compilation.

Covers GitHub issue MoonLadderStudios/MoonMind#3514 required work item 6: an
authoring surface enables follow-up retrieval by writing
``parameters["followUpRetrieval"]``; the coordinator compiles a runtime
``followUpRetrieval`` block into the durable launch snapshot that the retrieval
gateway consumes. Follow-up retrieval is an authority boundary, so it stays
disabled unless explicitly enabled.
"""

from __future__ import annotations

import pytest

from api_service.api.routers.retrieval_gateway import (
    BridgeRetrievalCapabilityIssue,
    _bridge_authoritative_issue,
)
from moonmind.omnigent.execution_profiles import (
    validate_effective_launch_snapshot,
)
from moonmind.omnigent.oauth_hosts import OmnigentOAuthHostError
from moonmind.omnigent.profile_bound_execution import (
    _compile_persisted_effective_launch,
    compile_follow_up_retrieval_policy,
    enforce_required_follow_up_retrieval,
)


def _policy_snapshot() -> dict:
    return {
        "policyRef": "codex-static@7",
        "policyVersion": 7,
        "policyDigest": "sha256:deadbeef",
        "boundaries": {
            "rag": {
                "initialScope": "workflow",
                "followupScope": "session",
                "collectionRefs": ["repo", "docs"],
                "tokenBudget": 6000,
                "latencyBudgetMs": 4000,
                "fallback": "deny",
                "credentialRef": "retrieval-profile",
            }
        },
    }


def test_follow_up_retrieval_disabled_by_default() -> None:
    assert compile_follow_up_retrieval_policy(
        _policy_snapshot(), {}, repository="MoonMind", tenant_id="tenant-1"
    ) == {"enabled": False}
    assert compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {"followUpRetrieval": {"enabled": False}},
        repository="MoonMind",
        tenant_id="tenant-1",
    ) == {"enabled": False}


def test_follow_up_retrieval_compiles_authored_block() -> None:
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {
            "followUpRetrieval": {
                "enabled": True,
                "required": True,
                "collections": ["repo", "repo", "docs"],
                "filters": {"branch": "main", "": "skip", "empty": ""},
                "topK": 5,
                "maxContextTokens": 4000,
                "latencyMs": 2500,
                "overlayPolicy": "skip",
                "staleOverlayAllowed": True,
                "fallbackAllowed": True,
                "maxLifetimeSeconds": 300,
            }
        },
        repository="MoonMind",
        tenant_id="tenant-1",
    )

    assert block["enabled"] is True
    assert block["required"] is True
    assert block["repository"] == "MoonMind"
    assert block["tenantId"] == "tenant-1"
    assert block["policyVersion"] == "codex-static@7"
    # Duplicate collections are de-duplicated while order is preserved.
    assert block["collections"] == ["repo", "docs"]
    # Blank filter keys/values are dropped.
    assert block["filters"] == {"branch": "main"}
    assert block["topK"] == 5
    assert block["maxContextTokens"] == 4000
    assert block["latencyMs"] == 2500
    assert block["overlayPolicy"] == "skip"
    assert block["staleOverlayAllowed"] is True
    assert block["fallbackAllowed"] is True
    assert block["maxLifetimeSeconds"] == 300


def test_follow_up_retrieval_folds_policy_budgets_when_unauthored() -> None:
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {"followUpRetrieval": {"enabled": True, "collections": ["repo"]}},
        repository="MoonMind",
        tenant_id="tenant-1",
    )
    # boundaries.rag budgets reach the compiled block as the per-run ceiling.
    assert block["latencyMs"] == 4000
    assert block["maxContextTokens"] == 6000


def test_follow_up_retrieval_clamps_authored_budgets_to_policy() -> None:
    # Authored budgets larger than the selected policy must be clamped down; the
    # gateway only clamps against deployment env limits, not the selected policy.
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {
            "followUpRetrieval": {
                "enabled": True,
                "collections": ["repo"],
                "maxContextTokens": 9000,
                "latencyMs": 9000,
            }
        },
        repository="MoonMind",
        tenant_id="tenant-1",
    )
    # policy tokenBudget=6000, latencyBudgetMs=4000 win over the larger authored
    # values.
    assert block["maxContextTokens"] == 6000
    assert block["latencyMs"] == 4000


def test_follow_up_retrieval_keeps_tighter_authored_budget() -> None:
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {
            "followUpRetrieval": {
                "enabled": True,
                "collections": ["repo"],
                "maxContextTokens": 1000,
                "latencyMs": 1500,
            }
        },
        repository="MoonMind",
        tenant_id="tenant-1",
    )
    # Authored values below the policy ceiling are preserved (they only narrow).
    assert block["maxContextTokens"] == 1000
    assert block["latencyMs"] == 1500


def test_enforce_required_follow_up_retrieval_blocks_unavailable() -> None:
    authored = {"enabled": True, "required": True}
    compiled = {"enabled": False, "reason": "incomplete_follow_up_retrieval_scope"}
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        enforce_required_follow_up_retrieval(authored, compiled)
    assert excinfo.value.code == "OMNIGENT_REQUIRED_FOLLOW_UP_RETRIEVAL_UNAVAILABLE"


def test_enforce_required_follow_up_retrieval_allows_optional_and_available() -> None:
    # Optional retrieval may degrade silently.
    enforce_required_follow_up_retrieval(
        {"enabled": True, "required": False}, {"enabled": False}
    )
    # Not enabled at all: required is moot.
    enforce_required_follow_up_retrieval(
        {"enabled": False, "required": True}, {"enabled": False}
    )
    # Required and available: no error.
    enforce_required_follow_up_retrieval(
        {"enabled": True, "required": True}, {"enabled": True}
    )


def test_follow_up_retrieval_incomplete_scope_disables_with_reason() -> None:
    # Enabled but no collections and no resolvable repository/tenant.
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {"followUpRetrieval": {"enabled": True}},
        repository="",
        tenant_id="",
    )
    assert block == {
        "enabled": False,
        "reason": "incomplete_follow_up_retrieval_scope",
    }


def test_compiled_block_flows_through_gateway_issue() -> None:
    """The compiled snapshot block satisfies the gateway capability issuer."""
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {
            "followUpRetrieval": {
                "enabled": True,
                "collections": ["repo", "docs"],
                "topK": 6,
            }
        },
        repository="MoonMind",
        tenant_id="tenant-1",
    )

    row = type(
        "BridgeRow",
        (),
        {
            "status": "active",
            "bridge_session_id": "bridge-1",
            "moonmind_workflow_id": "workflow-1",
            "moonmind_agent_run_id": "agent-run-1",
            "omnigent_host_id": "host-1",
            "omnigent_session_id": "session-1",
            "moonmind_run_id": "run-1",
            "step_execution_id": "step-1",
            "workspace": "workspace-1",
            "effective_launch_snapshot_json": {"followUpRetrieval": block},
        },
    )()

    issue = _bridge_authoritative_issue(
        row, BridgeRetrievalCapabilityIssue(collections=["docs"], top_k=3)
    )
    assert issue.tenant_id == "tenant-1"
    assert issue.repository == "MoonMind"
    assert issue.policy_version == "codex-static@7"
    # Host narrowing is honored within the compiled ceiling.
    assert issue.collections == ["docs"]
    assert issue.top_k == 3


def test_effective_launch_snapshot_carries_and_validates_block() -> None:
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {"followUpRetrieval": {"enabled": True, "collections": ["repo"]}},
        repository="MoonMind",
        tenant_id="tenant-1",
    )
    # Minimal boundaries needed by _compile_persisted_effective_launch.
    snapshot = _policy_snapshot()
    snapshot["boundaries"].update(
        {
            "host": {
                "mode": "static_compose",
                "backendRef": "backend",
                "architectures": ["amd64"],
                "serverImageRef": "server:1",
                "hostImageRef": "host:1",
            },
            "execution": {
                "profileRef": "omnigent-codex@1",
                "agentIdentities": ["codex-native-ui"],
                "harness": "codex",
            },
            "endpoint": {"ref": "endpoint:1"},
            "resources": {
                "cpuMillis": 1000,
                "memoryMiB": 2048,
                "processes": 64,
                "timeoutSeconds": 900,
                "temporaryStorageMiB": 1024,
            },
            "network": {"attachmentRef": "net", "egressProfileRef": "egress"},
            "workspace": {
                "mountClasses": ["repo"],
                "repositoryMutation": True,
                "runtimeUid": 1000,
                "runtimeGid": 1000,
            },
            "session": {"cleanup": "drain"},
            "retention": {"days": 30},
            "capture": {"logs": True},
        }
    )

    realized = _compile_persisted_effective_launch(
        snapshot, provider_profile_id="profile-1", follow_up_retrieval=block
    )
    assert realized["followUpRetrieval"]["enabled"] is True
    assert realized["followUpRetrieval"]["repository"] == "MoonMind"
    # Digest must still validate with the block inside it.
    validate_effective_launch_snapshot(realized)


def test_effective_launch_snapshot_defaults_to_disabled() -> None:
    # Build a minimal valid snapshot and omit follow_up_retrieval entirely.
    base = _policy_snapshot()
    base["boundaries"].update(
        {
            "host": {
                "mode": "static_compose",
                "backendRef": "backend",
                "architectures": ["amd64"],
                "serverImageRef": "server:1",
                "hostImageRef": "host:1",
            },
            "execution": {
                "profileRef": "omnigent-codex@1",
                "agentIdentities": ["codex-native-ui"],
                "harness": "codex",
            },
            "endpoint": {"ref": "endpoint:1"},
            "resources": {
                "cpuMillis": 1000,
                "memoryMiB": 2048,
                "processes": 64,
                "timeoutSeconds": 900,
                "temporaryStorageMiB": 1024,
            },
            "network": {"attachmentRef": "net", "egressProfileRef": "egress"},
            "workspace": {
                "mountClasses": ["repo"],
                "repositoryMutation": True,
                "runtimeUid": 1000,
                "runtimeGid": 1000,
            },
            "session": {"cleanup": "drain"},
            "retention": {"days": 30},
            "capture": {"logs": True},
        }
    )
    realized = _compile_persisted_effective_launch(
        base, provider_profile_id="profile-1"
    )
    assert realized["followUpRetrieval"] == {"enabled": False}
    validate_effective_launch_snapshot(realized)
