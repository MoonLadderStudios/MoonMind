"""Specification and trace assertions for RGS transactions (#5194)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/security/rgs-scenarios.json"


def inputs():
    from moonmind.security.rgs_assessment import RgsScenarioSpec, RgsTraceBundle

    data = json.loads(FIXTURE.read_text())
    return RgsScenarioSpec.model_validate(data["spec"]), RgsTraceBundle.model_validate(
        data["traces"]
    )


def assess(spec=None, traces=None, **kwargs):
    from moonmind.security.rgs_assessment import assess_rgs

    if spec is None:
        spec, traces = inputs()
    return assess_rgs(spec, traces, **kwargs)


def test_reference_traces_distinguish_wager_and_cashout_boundaries():
    result = assess()
    assert (result.status, result.outcome) == ("complete", "consistent")
    decisions = {
        (row.scenario, row.operation): row.expected
        for row in result.checks
        if row.rule == "operation-decision"
    }
    assert decisions["boundaries", "wager-early"] == "accepted"
    assert decisions["boundaries", "wager-late"] == "denied"
    assert decisions["boundaries", "cash-early"] == "accepted"
    assert decisions["boundaries", "cash-late"] == "denied"
    assert decisions["boundaries", "cash-tie"] == "denied"
    assert result.spec == inputs()[0]
    assert {"transport_not_exercised", "authorization_matrix_not_exercised"} <= set(
        result.limitations
    )


def test_received_cashout_and_tie_rules_come_from_the_specification():
    spec, traces = inputs()
    spec.cashout_close.recognition = "received"
    spec.cashout_close.tie = "allow"
    # Recognized before the boundary although delivered after it; a different
    # wager at the exact boundary is legal only under the newly declared rule.
    result = assess(spec, traces)
    checks = {r.operation: r for r in result.checks if r.rule == "operation-decision"}
    assert checks["cash-tie"].expected == "accepted"
    assert checks["cash-tie"].decision == "violation"
    assert checks["cash-reordered"].expected == "accepted"


@pytest.mark.parametrize(
    "change",
    [
        "unknown-state",
        "broken-transition",
        "missing-close",
        "duplicate-operation",
        "reordered-processing",
        "unknown-wager",
        "invalid-wallet-amount",
    ],
)
def test_contradictory_inputs_are_rejected(change):
    from moonmind.security.rgs_assessment import RgsScenarioSpec

    raw = json.loads(FIXTURE.read_text())["spec"]
    scenario = raw["scenarios"][0]
    if change == "unknown-state":
        raw["allowedStates"]["wager"] = ["invented"]
    elif change == "broken-transition":
        raw["transitions"][1]["fromState"] = "open"
    elif change == "missing-close":
        scenario["events"] = scenario["events"][1:]
    elif change == "duplicate-operation":
        scenario["operations"].append(copy.deepcopy(scenario["operations"][-1]))
    elif change == "reordered-processing":
        scenario["operations"][1]["processedAt"] = 0
    elif change == "unknown-wager":
        scenario["operations"][0]["wagerRef"] = "missing"
    else:
        scenario["wagers"][0]["stake"] = 1.5
    with pytest.raises(ValueError):
        RgsScenarioSpec.model_validate(raw)


@pytest.mark.parametrize(
    "fault,rule",
    [
        ("double-debit", "wallet-effect"),
        ("double-credit", "wallet-effect"),
        ("missing-audit", "audit-effect"),
        ("wrong-balance", "wallet-balance"),
        ("wrong-outcome", "wager-outcome"),
        ("wrong-round", "round-state"),
    ],
)
def test_financial_and_three_way_reconciliation_defects_are_observable(fault, rule):
    from tests.fixtures.security.rgs_reference import reference_traces

    spec, _ = inputs()
    traces = reference_traces(spec, fault=fault)
    result = assess(spec, traces)
    assert result.outcome == "violations"
    assert any(
        row.rule == rule and row.decision == "violation" for row in result.checks
    )
    assert all(row.evidence_refs for row in result.checks)


def test_duplicate_replay_competing_cashouts_reconnect_and_rollback():
    from tests.fixtures.security.rgs_reference import reference_traces

    spec, _ = inputs()
    traces = reference_traces(spec)
    result = assess(spec, traces)
    assert result.outcome == "consistent"
    race = next(t for t in traces.traces if t.scenario == "recovery")
    assert [r.decision for r in race.operations] == [
        "accepted",
        "accepted",
        "accepted",
        "unknown",
        "accepted",
        "denied",
        "accepted",
        "accepted",
        "accepted",
        "accepted",
    ]
    assert race.operations[3].reconciled_by == "cash-retry"
    assert len([e for e in race.wallet_effects if e.kind == "credit"]) == 1
    assert len([e for e in race.wallet_effects if e.kind == "rollback"]) == 1
    assert race.balances == {"wallet-a": 1050}
    assert race.wager_outcomes == {"first": "cashed_out", "second": "rolled_back"}


@pytest.mark.parametrize(
    "stop,reason",
    [
        ("cancel", "cancelled"),
        ("operations", "operation_budget"),
        ("time", "time_budget"),
    ],
)
def test_stops_keep_completed_checks_and_incomplete_coverage(stop, reason):
    kwargs = {}
    if stop == "cancel":
        calls = iter([False, False, True])
        kwargs["should_stop"] = lambda: next(calls, True)
    elif stop == "operations":
        kwargs["max_operations"] = 2
    else:
        ticks = iter([0, 0, 0, 2])
        kwargs.update(clock=lambda: next(ticks, 2), max_seconds=1)
    result = assess(**kwargs)
    assert (result.status, result.outcome) == ("partial", "incomplete")
    assert reason in result.limitations
    assert any(c.decision == "consistent" for c in result.checks)
    assert result.completed_operations < result.planned_operations


def test_interrupted_trace_retains_confirmed_credit_and_cannot_claim_consistency():
    spec, traces = inputs()
    recovery = next(t for t in traces.traces if t.scenario == "recovery")
    recovery.complete = False
    recovery.operations = recovery.operations[:4]
    recovery.operations[-1].decision = "unknown"
    result = assess(spec, traces)
    assert result.status == "partial"
    assert result.outcome == "incomplete"
    assert any(
        c.observed == "unknown" and c.decision == "incomplete" for c in result.checks
    )
    # Evaluation is pure. Reprocessing evidence never sends another wager,
    # debit or credit; the input retains the already confirmed financial effect.
    assert assess(spec, traces).model_dump() == result.model_dump()
    assert len([e for e in recovery.wallet_effects if e.kind == "credit"]) == 1


def test_incomplete_reports_and_digest_tampering_are_rejected():
    from moonmind.security.rgs_assessment import RgsAssessment

    result = assess(max_operations=1).model_dump(mode="json", by_alias=True)
    result.update(status="complete", outcome="consistent")
    with pytest.raises(ValueError):
        RgsAssessment.model_validate(result)
    result = assess().model_dump(mode="json", by_alias=True)
    result["spec"]["cashoutClose"]["tie"] = "allow"
    with pytest.raises(ValueError):
        RgsAssessment.model_validate(result)


def test_unknown_trace_identity_and_extra_receipts_are_rejected():
    spec, traces = inputs()
    traces.traces[0].scenario = "another-scenario"
    with pytest.raises(ValueError):
        assess(spec, traces)
    spec, traces = inputs()
    traces.traces[0].operations[0].operation = "not-an-admitted-operation"
    with pytest.raises(ValueError):
        assess(spec, traces)


def test_a_stop_during_final_reporting_cannot_become_complete_coverage():
    now = [0.0]

    def progress(result):
        if result.completed_operations == result.planned_operations:
            now[0] = 2.0

    result = assess(max_seconds=1, clock=lambda: now[0], on_progress=progress)
    assert result.completed_operations == result.planned_operations
    assert (result.status, result.outcome, result.stop_reason) == (
        "partial",
        "incomplete",
        "time_budget",
    )


def test_complete_report_requires_every_final_reconciliation_check():
    from moonmind.security.rgs_assessment import RgsAssessment

    raw = assess().model_dump(mode="json", by_alias=True)
    raw["checks"] = [row for row in raw["checks"] if row["rule"] != "wallet-balance"]
    with pytest.raises(ValueError):
        RgsAssessment.model_validate(raw)


def test_concurrent_cashouts_linearize_once_and_both_serial_orders_are_legal():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from moonmind.security.rgs_assessment import RgsScenarioSpec, RgsTraceBundle
    from tests.fixtures.security.rgs_reference import GameServer

    spec, _ = inputs()
    raw = spec.model_dump(mode="json", by_alias=True)
    scenario = raw["scenarios"][1]
    wager = scenario["operations"][0]
    cashouts = [
        copy.deepcopy(scenario["operations"][4]),
        copy.deepcopy(scenario["operations"][5]),
    ]
    for cash in cashouts:
        cash["processedAt"] = cash["receivedAt"] = 11
    for order in (cashouts, list(reversed(cashouts))):
        target = GameServer(raw, scenario)
        assert target.request(wager) == "accepted"
        barrier = Barrier(2)

        def competing(request, barrier=barrier, target=target):
            barrier.wait(timeout=5)
            return request, target.request(request)

        with ThreadPoolExecutor(max_workers=2) as pool:
            observed = list(pool.map(competing, order))
        assert sorted(decision for _, decision in observed) == ["accepted", "denied"]
        assert len([e for e in target.wallet.effects if e["kind"] == "credit"]) == 1
        # Record the legal linearization, then replay it against the same
        # wallet receipts; no confirmed effect may be repeated.
        observed.sort(key=lambda pair: pair[1] == "denied")
        scenario["operations"] = [wager, *(request for request, _ in observed)]
        raw["scenarios"] = [scenario]
        trace = target.trace()
        result = assess(
            RgsScenarioSpec.model_validate(raw),
            RgsTraceBundle.model_validate(
                {"sourceRef": "fixture:concurrent-cashouts", "traces": [trace]}
            ),
        )
        assert result.outcome == "consistent"
        assert target.wallet.balances == {"wallet-a": 1050}


def access_inputs():
    """An explicit target matrix, independent of MoonMind operator access."""
    raw = json.loads(FIXTURE.read_text())["spec"]
    raw["scenarios"] = [raw["scenarios"][0]]
    raw["authorizationMatrix"] = {
        "identities": [
            {
                "id": "owner",
                "accountRef": "wallet-a",
                "sessionRef": "session-a",
                "integrationRef": "integration-a",
                "credentialRef": "secret://target/a",
            },
            {
                "id": "other",
                "accountRef": "wallet-b",
                "sessionRef": "session-b",
                "integrationRef": "integration-b",
                "credentialRef": "secret://target/b",
            },
        ],
        "cases": [
            {
                "id": "own",
                "identityRef": "owner",
                "accountRef": "wallet-a",
                "sessionRef": "session-a",
                "integrationRef": "integration-a",
                "action": "wager",
                "expected": "allow",
            },
            {
                "id": "cross-account",
                "identityRef": "other",
                "accountRef": "wallet-a",
                "sessionRef": "session-b",
                "integrationRef": "integration-b",
                "action": "wager",
                "expected": "deny",
            },
        ],
    }
    scenario = raw["scenarios"][0]
    scenario["operations"] = [scenario["operations"][0]]
    scenario["operations"][0]["accessCaseRef"] = "own"
    return raw


def test_declared_target_access_changes_expected_financial_outcomes():
    from moonmind.security.rgs_assessment import RgsScenarioSpec

    _, traces = inputs()
    raw = access_inputs()
    result = assess(
        RgsScenarioSpec.model_validate(raw), traces.model_copy(update={"traces": []})
    )
    assert result.checks[0].expected == "accepted"
    raw["authorizationMatrix"]["cases"][0]["expected"] = "deny"
    result = assess(
        RgsScenarioSpec.model_validate(raw), traces.model_copy(update={"traces": []})
    )
    assert result.checks[0].expected == "denied"
    assert not any(c.rule == "wallet-effect" for c in result.checks)


@pytest.mark.parametrize(
    "change",
    [
        "duplicate-identity",
        "unknown-identity",
        "contradiction",
        "unknown-case",
        "wrong-account",
        "wrong-action",
        "missing-case",
        "orphan-case",
    ],
)
def test_target_access_contract_rejects_ambiguous_cases(change):
    from moonmind.security.rgs_assessment import RgsScenarioSpec

    raw = access_inputs()
    matrix = raw["authorizationMatrix"]
    operation = raw["scenarios"][0]["operations"][0]
    if change == "duplicate-identity":
        matrix["identities"].append(copy.deepcopy(matrix["identities"][0]))
    elif change == "unknown-identity":
        matrix["cases"][0]["identityRef"] = "missing"
    elif change == "contradiction":
        other = {**matrix["cases"][0], "id": "contradictory", "expected": "deny"}
        matrix["cases"].append(other)
    elif change == "unknown-case":
        operation["accessCaseRef"] = "missing"
    elif change == "wrong-account":
        matrix["cases"][0]["accountRef"] = "wallet-b"
    elif change == "wrong-action":
        matrix["cases"][0]["action"] = "cashout"
    elif change == "missing-case":
        del operation["accessCaseRef"]
    else:
        del raw["authorizationMatrix"]
    with pytest.raises(ValueError):
        RgsScenarioSpec.model_validate(raw)


def test_denied_actor_cannot_replay_an_allowed_transaction():
    from moonmind.security.rgs_assessment import RgsScenarioSpec, RgsTraceBundle

    raw = access_inputs()
    scenario = raw["scenarios"][0]
    original = scenario["operations"][0]
    scenario["operations"].append(
        {**original, "id": "cross-replay", "accessCaseRef": "cross-account"}
    )
    result = assess(
        RgsScenarioSpec.model_validate(raw),
        RgsTraceBundle(source_ref="fixture:access", traces=[]),
    )
    assert [
        (c.operation, c.expected)
        for c in result.checks
        if c.rule == "operation-decision"
    ] == [(original["id"], "accepted"), ("cross-replay", "denied")]
