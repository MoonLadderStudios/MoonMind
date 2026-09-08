"""Retired built-in vector retrieval → launch snapshot compilation.

MoonLadderStudios/MoonMind#4105: newly compiled plans carry no vector
capability descriptors. ``compile_follow_up_retrieval_policy`` always returns
``{"enabled": False}``; any explicit authored request fails via
``enforce_required_follow_up_retrieval`` before host launch, and gateway
issuance from an enabled launch snapshot fails retired (410) without minting
authority. Historical snapshots remain readable downstream.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api_service.api.routers.retrieval_gateway import (
    BridgeRetrievalCapabilityIssue,
    _bridge_authoritative_issue,
)
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.codex_execution_decisions import (
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


def test_follow_up_retrieval_retired_even_when_explicitly_enabled() -> None:
    """Explicit authored blocks compile to disabled: no new vector authority."""
    block = compile_follow_up_retrieval_policy(
        _policy_snapshot(),
        {
            "followUpRetrieval": {
                "enabled": True,
                "required": True,
                "collections": ["repo", "docs"],
                "topK": 5,
                "maxContextTokens": 4000,
            }
        },
        repository="MoonMind",
        tenant_id="tenant-1",
    )
    assert block == {"enabled": False}


def test_enforce_retired_vector_retrieval_blocks_explicit() -> None:
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        enforce_required_follow_up_retrieval(
            {"enabled": True, "required": True}, {"enabled": False}
        )
    assert excinfo.value.code == "OMNIGENT_RETIRED_VECTOR_RETRIEVAL"
    # Optional-but-enabled is still an explicit retired request: no silent weaken.
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        enforce_required_follow_up_retrieval(
            {"enabled": True, "collections": ["repo"]}, {"enabled": False}
        )
    assert excinfo.value.code == "OMNIGENT_RETIRED_VECTOR_RETRIEVAL"


def test_enforce_retired_vector_retrieval_allows_absent() -> None:
    enforce_required_follow_up_retrieval(None, {"enabled": False})
    enforce_required_follow_up_retrieval({}, {"enabled": False})
    enforce_required_follow_up_retrieval(
        {"enabled": False}, {"enabled": False}
    )
    enforce_required_follow_up_retrieval(
        {"enabled": False, "required": False}, {"enabled": False}
    )


def test_gateway_issuance_from_enabled_snapshot_is_retired() -> None:
    """New capability issuance from a retired enabled snapshot fails 410."""
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
            "effective_launch_snapshot_json": {
                "followUpRetrieval": {
                    "enabled": True,
                    "repository": "MoonMind",
                    "tenantId": "tenant-1",
                    "policyVersion": "codex-static@7",
                    "collections": ["repo", "docs"],
                }
            },
        },
    )()

    with pytest.raises(HTTPException) as excinfo:
        _bridge_authoritative_issue(
            row, BridgeRetrievalCapabilityIssue(collections=["docs"], top_k=3)
        )
    assert excinfo.value.status_code == 410
