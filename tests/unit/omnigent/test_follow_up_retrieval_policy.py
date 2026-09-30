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


def test_follow_up_retrieval_always_compiles_disabled() -> None:
    """No launch input can mint follow-up retrieval authority any more."""
    assert compile_follow_up_retrieval_policy() == {"enabled": False}


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
