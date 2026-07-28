from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from api_service.api.routers.retrieval_gateway import (
    RetrievalCorrelation,
    RetrievalQuery,
    _effective_session_request,
    _enforce_session_result_budget,
)
from api_service.retrieval_capabilities import (
    RetrievalBudgetSnapshot,
    RetrievalCapabilityError,
    RetrievalCapabilityRegistry,
)
from moonmind.rag.context_pack import ContextItem, build_context_pack


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


def test_capability_accounting_and_revocation_survive_registry_restart(tmp_path) -> None:
    first = RetrievalCapabilityRegistry(tmp_path)
    token, capability = first.issue(_budget(max_queries=2), lifetime_seconds=60)
    first.begin(capability, "tool-1")
    first.finish(capability, "tool-1", {"deliveryState": "delivery_unknown"})

    restarted = RetrievalCapabilityRegistry(tmp_path)
    restored = restarted.resolve(
        token, host_id="host-1", session_id="session-1", run_id="run-1"
    )
    assert restored.query_count == 1
    assert restarted.begin(restored, "tool-1") == {
        "deliveryState": "delivery_unknown"
    }

    restarted.revoke(restored.capability_id)
    after_revoke = RetrievalCapabilityRegistry(tmp_path)
    with pytest.raises(RetrievalCapabilityError) as revoked:
        after_revoke.resolve(
            token, host_id="host-1", session_id="session-1", run_id="run-1"
        )
    assert revoked.value.reason == "revoked"


def test_evidence_index_and_artifact_backed_result_survive_restart(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(_budget(), lifetime_seconds=60)
    result_ref = registry.store_result(
        capability, "tool-1", {"items": [{"text": "large result"}]}
    )
    evidence_ref = registry.record(
        capability,
        {
            "state": "succeeded",
            "contextPackRef": result_ref,
            "delivery": {"state": "delivery_unknown"},
        },
    )

    restarted = RetrievalCapabilityRegistry(tmp_path)
    status = restarted.status(capability.capability_id)
    assert status["requests"][0]["evidenceRef"] == evidence_ref
    assert status["requests"][0]["delivery"]["state"] == "delivery_unknown"
    assert next((tmp_path / "run-1" / "results").glob("*.json")).exists()


def test_delivery_requires_explicit_bridge_acknowledgement(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(_budget(), lifetime_seconds=60)
    registry.begin(capability, "tool-1")
    registry.finish(
        capability,
        "tool-1",
        {
            "deliveryState": "delivery_unknown",
            "deliveryBoundary": "typed_continuation",
        },
    )

    acknowledged = registry.acknowledge_delivery(
        capability.capability_id, "tool-1", state="delivered"
    )
    assert acknowledged["deliveryState"] == "delivered"

    restarted = RetrievalCapabilityRegistry(tmp_path)
    assert restarted.begin(capability, "tool-1")["deliveryState"] == "delivered"


def test_per_minute_rate_limit_is_durable_and_deduplicated(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(
        _budget(max_queries=10, max_requests_per_minute=1), lifetime_seconds=60
    )
    registry.begin(capability, "tool-1")
    registry.finish(capability, "tool-1", {"deliveryState": "delivery_unknown"})

    restarted = RetrievalCapabilityRegistry(tmp_path)
    restored = restarted.status(capability.capability_id)
    assert restored["requestsInCurrentMinute"] == 1
    assert restarted.begin(capability, "tool-1") == {
        "deliveryState": "delivery_unknown"
    }
    with pytest.raises(RetrievalCapabilityError) as limited:
        restarted.begin(capability, "tool-2")
    assert limited.value.reason == "rate_exceeded"


def test_authorization_denial_is_correlated_without_exposing_token(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = registry.issue(_budget(), lifetime_seconds=60)

    with pytest.raises(RetrievalCapabilityError) as mismatch:
        registry.resolve(
            token,
            host_id="other-host",
            session_id="session-1",
            run_id="run-1",
            denial_context={"stepId": "step-1", "toolCallId": "tool-7"},
        )
    assert mismatch.value.reason == "identity_mismatch"

    request = registry.status(capability.capability_id)["requests"][0]
    assert request["state"] == "denied"
    assert request["classification"] == "identity_mismatch"
    evidence_path = next(tmp_path.rglob("retrieval_*.json"))
    assert token not in evidence_path.read_text(encoding="utf-8")


def test_revoke_scope_drains_all_matching_session_authority(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    first_token, first = registry.issue(_budget(), lifetime_seconds=60)
    second_token, second = registry.issue(
        _budget(session_id="session-2"), lifetime_seconds=60
    )

    revoked = registry.revoke_scope(run_id="run-1", host_id="host-1")
    assert set(revoked) == {first.capability_id, second.capability_id}
    for token, session_id in (
        (first_token, "session-1"),
        (second_token, "session-2"),
    ):
        with pytest.raises(RetrievalCapabilityError) as drained:
            registry.resolve(
                token, host_id="host-1", session_id=session_id, run_id="run-1"
            )
        assert drained.value.reason == "revoked"


@pytest.mark.parametrize(
    ("budget_overrides", "items", "usage", "elapsed_ms", "reason"),
    [
        (
            {"max_sources": 1},
            [
                ContextItem(score=1, source="a", text="a"),
                ContextItem(score=1, source="b", text="b"),
            ],
            {"tokens": 2},
            1,
            "source_budget_exhausted",
        ),
        (
            {"max_context_bytes": 8},
            [ContextItem(score=1, source="a", text="long context")],
            {"tokens": 2},
            1,
            "byte_budget_exhausted",
        ),
        (
            {"max_context_tokens": 1},
            [ContextItem(score=1, source="a", text="a")],
            {"tokens": 2},
            1,
            "token_budget_exhausted",
        ),
        (
            {"latency_ms": 1},
            [ContextItem(score=1, source="a", text="a")],
            {"tokens": 1},
            2,
            "latency_budget_exhausted",
        ),
    ],
)
def test_provider_result_cannot_broaden_capability_budget(
    tmp_path, budget_overrides, items, usage, elapsed_ms, reason
) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = registry.issue(
        _budget(**budget_overrides), lifetime_seconds=60
    )
    pack = build_context_pack(
        items=items,
        filters={},
        budgets={},
        usage=usage,
        transport="direct",
        telemetry_id="test",
        max_chars=1200,
    )

    with pytest.raises(RetrievalCapabilityError) as denied:
        _enforce_session_result_budget(pack, capability, elapsed_ms=elapsed_ms)
    assert denied.value.reason == reason
