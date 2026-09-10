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


def test_enforce_retired_vector_retrieval_blocks_zero_budget() -> None:
    """Zero budgets are explicit retired content (0 == False in Python)."""
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        enforce_required_follow_up_retrieval(
            {"maxContextTokens": 0}, {"enabled": False}
        )
    assert excinfo.value.code == "OMNIGENT_RETIRED_VECTOR_RETRIEVAL"
