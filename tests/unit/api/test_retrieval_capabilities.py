from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from api_service.api.routers.retrieval_gateway import (
    RetrievalCorrelation,
    RetrievalQuery,
    _effective_session_request,
)
from api_service.retrieval_capabilities import (
    RetrievalBudgetSnapshot,
    RetrievalCapabilityError,
    RetrievalCapabilityRegistry,
)


def _budget(**overrides):
    values = {
        "tenant_id": "tenant-1",
        "repository": "MoonMind",
        "run_id": "run-1",
        "workspace_id": "workspace-1",
        "host_id": "host-1",
        "session_id": "session-1",
        "step_id": "step-1",
        "policy_version": "policy-7",
        "collections": ("repo", "docs"),
        "filters": (),
        "max_queries": 2,
    }
    values.update(overrides)
    return RetrievalBudgetSnapshot(**values)


def test_capability_is_identity_bound_and_stores_only_token_digest(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = registry.issue(_budget(), lifetime_seconds=60)

    assert token not in repr(capability)
    assert registry.resolve(
        token, host_id="host-1", session_id="session-1", run_id="run-1"
    ) is capability
    with pytest.raises(RetrievalCapabilityError, match="does not belong"):
        registry.resolve(
            token, host_id="host-2", session_id="session-1", run_id="run-1"
        )


def test_query_accounting_deduplicates_and_enforces_ceiling(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(_budget(max_queries=1), lifetime_seconds=60)

    assert registry.begin(capability, "tool-1") is None
    result = {"kind": "retrieval_tool_result"}
    registry.finish(capability, "tool-1", result)
    assert registry.begin(capability, "tool-1") == result
    with pytest.raises(RetrievalCapabilityError, match="exhausted"):
        registry.begin(capability, "tool-2")


def test_revoke_and_expiry_are_deterministic(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = registry.issue(_budget(), lifetime_seconds=0)
    with pytest.raises(RetrievalCapabilityError) as expired:
        registry.resolve(
            token, host_id="host-1", session_id="session-1", run_id="run-1"
        )
    assert expired.value.reason == "expired"

    token, capability = registry.issue(_budget(), lifetime_seconds=60)
    registry.revoke(capability.capability_id)
    with pytest.raises(RetrievalCapabilityError) as revoked:
        registry.resolve(
            token, host_id="host-1", session_id="session-1", run_id="run-1"
        )
    assert revoked.value.reason == "revoked"


def test_evidence_is_bounded_and_contains_no_capability_secret(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = registry.issue(_budget(), lifetime_seconds=60)
    ref = registry.record(
        capability,
        {
            "state": "denied",
            "queryDigest": "abc",
            "correlation": {"toolCallId": "tool-1"},
        },
    )

    evidence_path = next(tmp_path.rglob("*.json"))
    serialized = evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(serialized)
    assert ref.startswith("artifact://retrieval-follow-up/run-1/")
    assert token not in serialized
    assert evidence["budgetSnapshot"]["policy_version"] == "policy-7"
    assert evidence["state"] == "denied"


def test_session_request_can_only_narrow_immutable_budget(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(_budget(top_k=5), lifetime_seconds=60)
    payload = RetrievalQuery(
        query="bounded query",
        filters={"repo": "MoonMind"},
        collections=["docs"],
        top_k=3,
        budgets={"tokens": 100, "latency_ms": 500},
        correlation=RetrievalCorrelation(
            workflow_id="workflow-1",
            step_id="step-1",
            bridge_session_id="bridge-1",
            omnigent_session_id="session-1",
            turn_id="turn-1",
            tool_call_id="tool-1",
        ),
    )

    top_k, budgets, collections = _effective_session_request(payload, capability)
    assert top_k == 3
    assert budgets == {"tokens": 100, "latency_ms": 500}
    assert collections == ["docs"]
    assert payload.filters["tenant_id"] == "tenant-1"
    assert payload.filters["run_id"] == "run-1"
    assert payload.filters["workspace_id"] == "workspace-1"

    payload.top_k = 6
    with pytest.raises(HTTPException) as denied:
        _effective_session_request(payload, capability)
    assert denied.value.status_code == 403
