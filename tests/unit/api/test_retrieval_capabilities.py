from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import asdict

import pytest

from api_service.retrieval_capabilities import (
    RetrievalBudgetSnapshot,
    RetrievalCapability,
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
        "workflow_id": "workflow-1",
        "bridge_session_id": "bridge-1",
        "policy_version": "policy-7",
        "collections": ("repo", "docs"),
        "filters": (),
        "max_queries": 2,
    }
    values.update(overrides)
    return RetrievalBudgetSnapshot(**values)


def _seed_issued_capability(
    registry: RetrievalCapabilityRegistry,
    budget: RetrievalBudgetSnapshot,
    *,
    lifetime_seconds: int,
) -> tuple[str, RetrievalCapability]:
    """Seed an already-issued capability for drain tests (retired #4107).

    Test-only white-box seeding for the drain-only contract: production
    ``RetrievalCapabilityRegistry.issue`` always raises ``retired`` and must
    never be used to mint authority. This inserts the durable row directly so
    revoke/resolve/begin/finish/abort/status/evidence assertions keep covering
    already-issued lifecycle, budget, redaction and idempotency behavior.
    """
    now = time.time()
    token = secrets.token_urlsafe(32)
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    capability = RetrievalCapability(
        capability_id=f"rcap_{secrets.token_hex(12)}",
        token_digest=token_digest,
        budget=budget,
        issued_at=now,
        expires_at=now + lifetime_seconds,
    )
    with registry._lock:
        registry._capabilities[capability.capability_id] = capability
        registry._by_digest[token_digest] = capability.capability_id
        with registry._connect() as connection:
            connection.execute(
                """INSERT INTO retrieval_capabilities
                   (capability_id, token_digest, budget_json, issued_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    capability.capability_id,
                    capability.token_digest,
                    json.dumps(asdict(budget), sort_keys=True),
                    capability.issued_at,
                    capability.expires_at,
                ),
            )
    return token, capability


def test_issue_refuses_new_issuance_after_retirement(tmp_path) -> None:
    """New native retrieval issuance fails closed (#4105/#4107)."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    with pytest.raises(RetrievalCapabilityError) as retired:
        registry.issue(_budget(), lifetime_seconds=60)
    assert retired.value.reason == "retired"


def test_capability_is_identity_bound_and_stores_only_token_digest(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)

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
    _, capability = _seed_issued_capability(registry, _budget(max_queries=1), lifetime_seconds=60)

    assert registry.begin(capability, "tool-1") is None
    result = {"kind": "retrieval_tool_result"}
    registry.finish(capability, "tool-1", result)
    assert registry.begin(capability, "tool-1") == result
    with pytest.raises(RetrievalCapabilityError, match="exhausted"):
        registry.begin(capability, "tool-2")


def test_revoke_and_expiry_are_deterministic(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=0)
    with pytest.raises(RetrievalCapabilityError) as expired:
        registry.resolve(
            token, host_id="host-1", session_id="session-1", run_id="run-1"
        )
    assert expired.value.reason == "expired"

    token, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
    registry.revoke(capability.capability_id)
    with pytest.raises(RetrievalCapabilityError) as revoked:
        registry.resolve(
            token, host_id="host-1", session_id="session-1", run_id="run-1"
        )
    assert revoked.value.reason == "revoked"


def test_evidence_is_bounded_and_contains_no_capability_secret(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
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


def test_capability_accounting_and_revocation_survive_registry_restart(tmp_path) -> None:
    first = RetrievalCapabilityRegistry(tmp_path)
    token, capability = _seed_issued_capability(first, _budget(max_queries=2), lifetime_seconds=60)
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
    _, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
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
    assert next((tmp_path / "run-1" / "results").rglob("*.json")).exists()


def test_delivery_requires_explicit_bridge_acknowledgement(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
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
    _, capability = _seed_issued_capability(registry, 
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
    token, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)

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


def test_revoke_scope_drains_only_the_exact_session_authority(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    first_token, first = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
    second_token, second = _seed_issued_capability(registry, 
        _budget(session_id="session-2", step_id="step-2"), lifetime_seconds=60
    )

    revoked = registry.revoke_scope(
        run_id="run-1", host_id="host-1", session_id="session-1", step_id="step-1"
    )
    assert revoked == [first.capability_id]
    with pytest.raises(RetrievalCapabilityError) as drained:
        registry.resolve(
            first_token, host_id="host-1", session_id="session-1", run_id="run-1"
        )
    assert drained.value.reason == "revoked"
    # The sibling session in the same run keeps its authority.
    assert registry.resolve(
        second_token, host_id="host-1", session_id="session-2", run_id="run-1"
    ).capability_id == second.capability_id


@pytest.mark.parametrize(
    "scope",
    [
        {"run_id": "run-1", "host_id": "host-1", "session_id": "", "step_id": "step-1"},
        {"run_id": "run-1", "host_id": "", "session_id": "session-1", "step_id": "step-1"},
        {"run_id": "run-1", "host_id": "host-1", "session_id": "session-1", "step_id": None},
    ],
)
def test_revoke_scope_refuses_partial_identity(tmp_path, scope) -> None:
    """A missing identifier must never be treated as a run-wide wildcard."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    token, _ = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)

    with pytest.raises(RetrievalCapabilityError) as refused:
        registry.revoke_scope(**scope)
    assert refused.value.reason == "incomplete_scope"
    assert registry.resolve(
        token, host_id="host-1", session_id="session-1", run_id="run-1"
    )


def test_issuance_accounting_reports_live_scope_capability(tmp_path) -> None:
    """Live scope accounting is what stops a retry multiplying the budget."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    scope = {
        "run_id": "run-1",
        "host_id": "host-1",
        "session_id": "session-1",
        "step_id": "step-1",
    }
    assert registry.live_scope_capability(**scope) is None

    _, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
    live = registry.live_scope_capability(**scope)
    assert live is not None and live.capability_id == capability.capability_id

    registry.revoke(capability.capability_id)
    assert registry.live_scope_capability(**scope) is None


def test_expired_reservation_is_reclaimed_after_process_interruption(tmp_path) -> None:
    """An abandoned in-progress request must not wedge the concurrency slot."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = _seed_issued_capability(registry, 
        _budget(max_queries=5, max_concurrency=1), lifetime_seconds=60
    )
    assert registry.begin(capability, "tool-1") is None

    # A second live request exceeds the concurrency ceiling...
    with pytest.raises(RetrievalCapabilityError) as busy:
        registry.begin(capability, "tool-2")
    assert busy.value.reason == "concurrency_exceeded"

    # ...but once the lease lapses (process died before finish/abort) the slot
    # is reclaimable instead of being lost until expiry.
    restarted = RetrievalCapabilityRegistry(tmp_path)
    with restarted._connect() as connection:
        connection.execute(
            "UPDATE retrieval_requests SET lease_expires_at = 1 WHERE state = 'in_progress'"
        )
    assert restarted.begin(capability, "tool-2") is None


def test_concurrent_retry_of_one_tool_call_is_reserved_not_duplicated(tmp_path) -> None:
    """The idempotency key must be reserved before execution, not after."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = _seed_issued_capability(registry, 
        _budget(max_queries=5, max_concurrency=4), lifetime_seconds=60
    )
    assert registry.begin(capability, "tool-1") is None

    with pytest.raises(RetrievalCapabilityError) as duplicate:
        registry.begin(capability, "tool-1")
    assert duplicate.value.reason == "duplicate_in_flight"
    # The rejected retry consumed no additional query budget.
    assert registry.status(capability.capability_id)["queryCount"] == 1

    registry.finish(capability, "tool-1", {"kind": "retrieval_tool_result"})
    assert registry.begin(capability, "tool-1") == {"kind": "retrieval_tool_result"}


def test_aborted_request_releases_its_reservation(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = _seed_issued_capability(registry, 
        _budget(max_queries=5, max_concurrency=1), lifetime_seconds=60
    )
    registry.begin(capability, "tool-1")
    registry.abort(capability, "tool-1")

    assert registry.status(capability.capability_id)["activeRequests"] == 0
    assert registry.begin(capability, "tool-2") is None


def test_assert_active_rejects_authority_revoked_in_flight(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
    registry.begin(capability, "tool-1")
    registry.assert_active(capability.capability_id)

    registry.revoke(capability.capability_id)
    with pytest.raises(RetrievalCapabilityError) as revoked:
        registry.assert_active(capability.capability_id)
    assert revoked.value.reason == "revoked"


def test_stored_results_are_namespaced_by_capability(tmp_path) -> None:
    """Two sessions in one run may reuse a tool-call id without colliding."""
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, first = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
    _, second = _seed_issued_capability(registry, 
        _budget(session_id="session-2", step_id="step-2"), lifetime_seconds=60
    )

    first_ref = registry.store_result(first, "tool-1", {"pack": "first"})
    second_ref = registry.store_result(second, "tool-1", {"pack": "second"})

    assert first_ref != second_ref
    assert registry.read_result(first, "tool-1") == {"pack": "first"}
    assert registry.read_result(second, "tool-1") == {"pack": "second"}


def test_stored_result_reference_is_dereferenceable(tmp_path) -> None:
    registry = RetrievalCapabilityRegistry(tmp_path)
    _, capability = _seed_issued_capability(registry, _budget(), lifetime_seconds=60)
    ref = registry.store_result(capability, "tool call/1", {"pack": "value"})

    assert ref == (
        f"/retrieval/capabilities/{capability.capability_id}"
        "/results/tool%20call%2F1"
    )
    assert registry.read_result(capability, "tool call/1") == {"pack": "value"}
    with pytest.raises(KeyError):
        registry.read_result(capability, "tool-missing")
