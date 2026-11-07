"""Specification-driven assertions over retained RGS transaction traces.

This evaluator performs no target or wallet operations. Admission, HTTP and
real-time transports, authorization matrices and job execution retain their
existing owners. A retry only rechecks immutable inputs, so it cannot repeat
a confirmed payment. Partial target traces remain incomplete until their
wallet, game and audit records can be reconciled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from moonmind.security.assessments import Contract, Evidence, Text, content_key
from moonmind.security.authorization_matrix import (
    AccessObservation,
    AuthorizationMatrix,
)
from moonmind.security.target_assessments import TargetObservation

Ident = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")]
Tick = Annotated[int, Field(strict=True, ge=0, le=10**15)]
Money = Annotated[int, Field(strict=True, ge=-(10**15), le=10**15)]
UnsignedMoney = Annotated[int, Field(strict=True, ge=0, le=10**15)]
Kind = Literal["wager", "cashout", "rollback", "settle", "reconnect"]
KINDS = {"wager", "cashout", "rollback", "settle", "reconnect"}
MAX_OPERATIONS = 4096
MAX_INPUT_BYTES = 8 * 1024 * 1024
LIMITATIONS = [
    "recorded_trace_only",
    "transport_not_exercised",
    "authorization_matrix_not_exercised",
]


def _unique(values):
    if len(values) != len(set(values)):
        raise ValueError("identities must be unique")


class Transition(Contract):
    event: Ident
    from_state: Ident
    to_state: Ident


class Boundary(Contract):
    event: Ident
    recognition: Literal["received", "processed"]
    tie: Literal["allow", "deny"]


class Interruption(Contract):
    event: Ident
    policy: Literal["resume", "rollback"]


class Event(Contract):
    event: Ident
    at: Tick


class Wager(Contract):
    id: Ident
    account_ref: Ident
    stake: Annotated[int, Field(strict=True, gt=0, le=10**15)]
    cashout_credit: UnsignedMoney
    settlement_credit: UnsignedMoney


class Operation(Contract):
    id: Ident
    kind: Kind
    transaction_id: Ident
    wager_ref: Ident | None = None
    received_at: Tick
    processed_at: Tick
    access_case_ref: Ident | None = None

    @model_validator(mode="after")
    def _valid(self):
        if (self.kind == "reconnect") != (self.wager_ref is None):
            raise ValueError("financial operations require a wager; reconnect has none")
        if self.received_at > self.processed_at:
            raise ValueError("processing cannot precede receipt")
        return self


class Scenario(Contract):
    id: Ident
    evidence_refs: Evidence
    events: Annotated[list[Event], Field(min_length=1, max_length=64)]
    initial_balances: Annotated[
        dict[Ident, UnsignedMoney], Field(min_length=1, max_length=64)
    ]
    wagers: Annotated[list[Wager], Field(min_length=1, max_length=128)]
    operations: Annotated[list[Operation], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def _valid(self):
        for values in (
            [e.event for e in self.events],
            [w.id for w in self.wagers],
            [o.id for o in self.operations],
        ):
            _unique(values)
        times = [e.at for e in self.events]
        if times != sorted(set(times)):
            raise ValueError("round events require strictly increasing times")
        if [o.processed_at for o in self.operations] != sorted(
            o.processed_at for o in self.operations
        ):
            raise ValueError("operations must declare their processing order")
        wagers = {w.id for w in self.wagers}
        if any(
            o.wager_ref is not None and o.wager_ref not in wagers
            for o in self.operations
        ):
            raise ValueError("operation references an unknown wager")
        if any(w.account_ref not in self.initial_balances for w in self.wagers):
            raise ValueError("wager requires its initial wallet balance")
        return self


class RgsScenarioSpec(Contract):
    schema_version: Literal["rgs-scenario-spec/v1"] = "rgs-scenario-spec/v1"
    game_ref: Text
    source_ref: Text
    integration_rules_ref: Text
    wallet_unit: Ident
    states: Annotated[list[Ident], Field(min_length=2, max_length=32)]
    initial_state: Ident
    transitions: Annotated[list[Transition], Field(min_length=1, max_length=64)]
    betting_close: Boundary
    cashout_close: Boundary
    allowed_states: dict[
        Kind, Annotated[list[Ident], Field(min_length=1, max_length=32)]
    ]
    interruption: Interruption
    scenarios: Annotated[list[Scenario], Field(min_length=1, max_length=32)]
    authorization_matrix: AuthorizationMatrix | None = None

    @model_validator(mode="after")
    def _valid(self):
        _unique(self.states)
        _unique([s.id for s in self.scenarios])
        _unique([(t.event, t.from_state) for t in self.transitions])
        if self.initial_state not in self.states or set(self.allowed_states) != KINDS:
            raise ValueError(
                "declare the initial state and each operation's allowed states"
            )
        used = {state for states in self.allowed_states.values() for state in states}
        used |= {s for t in self.transitions for s in (t.from_state, t.to_state)}
        if used - set(self.states):
            raise ValueError("unknown round state")
        if self.interruption.event not in {t.event for t in self.transitions}:
            raise ValueError("interruption requires a declared transition")
        transitions = {(t.event, t.from_state): t.to_state for t in self.transitions}
        for scenario in self.scenarios:
            wagers = {w.id: w for w in scenario.wagers}
            for operation in scenario.operations:
                if self.authorization_matrix is None:
                    if operation.access_case_ref is not None:
                        raise ValueError("access case requires a target matrix")
                    continue
                cases = {c.id: c for c in self.authorization_matrix.cases}
                case = cases.get(operation.access_case_ref)
                if case is None:
                    raise ValueError("every operation requires a declared access case")
                if case.action != operation.kind or (
                    operation.wager_ref
                    and case.account_ref != wagers[operation.wager_ref].account_ref
                ):
                    raise ValueError("access case contradicts the requested operation")
            state = self.initial_state
            for event in scenario.events:
                if (event.event, state) not in transitions:
                    raise ValueError("scenario contradicts the round-state transitions")
                state = transitions[event.event, state]
            events = {e.event: e.at for e in scenario.events}
            if (
                self.betting_close.event not in events
                or self.cashout_close.event not in events
            ):
                raise ValueError("scenario must include both declared close boundaries")
            if events[self.betting_close.event] > events[self.cashout_close.event]:
                raise ValueError("cashout cannot close before betting")
        if sum(len(s.operations) for s in self.scenarios) > MAX_OPERATIONS:
            raise ValueError("scenario operation ceiling exceeded")
        return self


class Receipt(Contract):
    operation: Ident
    decision: Literal["accepted", "denied", "unknown"]
    reconciled_by: Ident | None = None


class Effect(Contract):
    id: Ident
    transaction_id: Ident
    kind: Literal["debit", "credit", "rollback"]
    account_ref: Ident
    amount: Money

    @model_validator(mode="after")
    def _sign(self):
        if (self.kind == "debit" and self.amount >= 0) or (
            self.kind != "debit" and self.amount <= 0
        ):
            raise ValueError("wallet effect sign contradicts its operation")
        return self


class Trace(Contract):
    scenario: Ident
    evidence_refs: Evidence
    operations: Annotated[list[Receipt], Field(max_length=128)]
    wallet_effects: Annotated[list[Effect], Field(max_length=256)]
    audit_effects: Annotated[list[Effect], Field(max_length=256)]
    balances: Annotated[dict[Ident, Money], Field(max_length=64)]
    wager_outcomes: Annotated[
        dict[
            Ident, Literal["unplaced", "active", "cashed_out", "rolled_back", "settled"]
        ],
        Field(max_length=128),
    ]
    round_state: Ident
    complete: Annotated[bool, Field(strict=True)]
    access_observations: list[AccessObservation] | None = Field(None, max_length=128)

    @model_validator(mode="after")
    def _unique(self):
        _unique([r.operation for r in self.operations])
        if self.access_observations is not None:
            _unique([r.operation for r in self.access_observations])
        return self


class RgsTraceBundle(Contract):
    schema_version: Literal["rgs-traces/v1"] = "rgs-traces/v1"
    source_ref: Text
    traces: Annotated[list[Trace], Field(max_length=32)]
    collection: dict[Text, Text] | None = None
    observations: list[TargetObservation] | None = Field(None, max_length=8192)

    @model_validator(mode="after")
    def _unique(self):
        _unique([t.scenario for t in self.traces])
        return self


class Check(Contract):
    scenario: Ident
    operation: Ident
    rule: Literal[
        "operation-decision",
        "wallet-effect",
        "audit-effect",
        "wallet-balance",
        "wager-outcome",
        "round-state",
        "scenario-completion",
        "target-access",
    ]
    expected: Text
    observed: Text
    decision: Literal["consistent", "violation", "incomplete"]
    evidence_refs: Evidence


class JobOutcome(Contract):
    terminal_state: Text
    exit_code: int | None
    input_refs: dict[Ident, Text] = Field(default_factory=dict)


class RgsAssessment(Contract):
    schema_version: Literal["rgs-assessment/v1"] = "rgs-assessment/v1"
    spec: RgsScenarioSpec
    spec_digest: Text
    traces_digest: Text
    traces_source_ref: Text
    spec_input_ref: Text
    traces_input_ref: Text
    input_digests: dict[Literal["spec", "traces"], Text]
    retest: list[Text]
    checks: Annotated[list[Check], Field(max_length=40000)]
    planned_operations: Annotated[int, Field(ge=1, le=MAX_OPERATIONS)]
    completed_operations: Annotated[int, Field(ge=0, le=MAX_OPERATIONS)]
    status: Literal["complete", "partial"]
    outcome: Literal["consistent", "violations", "incomplete"]
    stop_reason: Literal["cancelled", "operation_budget", "time_budget"] | None = None
    job: JobOutcome | None = None
    limitations: list[Text]
    verification: Literal["not_performed"] = "not_performed"

    @model_validator(mode="after")
    def _consistent(self):
        if self.spec_digest != _digest(self.spec):
            raise ValueError("scenario specification digest mismatch")
        planned = {(s.id, o.id) for s in self.spec.scenarios for o in s.operations}
        decisions = [
            (c.scenario, c.operation)
            for c in self.checks
            if c.rule == "operation-decision"
        ]
        _unique([(c.scenario, c.operation, c.rule) for c in self.checks])
        completed = sum(
            c.rule == "operation-decision" and c.decision != "incomplete"
            for c in self.checks
        )
        if (
            not set(decisions) <= planned
            or self.planned_operations != len(planned)
            or self.completed_operations != completed
        ):
            raise ValueError("operation coverage differs from the specification")
        partial = (
            self.stop_reason is not None
            or set(decisions) != planned
            or any(c.decision == "incomplete" for c in self.checks)
        )
        partial |= bool(
            self.job
            and (self.job.terminal_state != "succeeded" or self.job.exit_code != 0)
        )
        # Every scenario needs a final reconciliation, even one with no trace.
        partial |= {
            c.scenario for c in self.checks if c.rule == "scenario-completion"
        } != {s.id for s in self.spec.scenarios}
        if self.spec.authorization_matrix is not None:
            partial |= {
                (c.scenario, c.operation)
                for c in self.checks
                if c.rule == "target-access"
            } != planned
        if not partial:
            required = set()
            for scenario in self.spec.scenarios:
                _, effects, balances, outcomes, _ = _expected(self.spec, scenario)
                required |= {
                    (scenario.id, rule, tx)
                    for tx in effects
                    for rule in ("wallet-effect", "audit-effect")
                }
                required |= {
                    (scenario.id, "wallet-balance", account) for account in balances
                }
                required |= {
                    (scenario.id, "wager-outcome", wager) for wager in outcomes
                }
                required.add((scenario.id, "round-state", scenario.id))
            partial |= not required <= {
                (c.scenario, c.rule, c.operation) for c in self.checks
            }
        outcome = (
            "violations"
            if any(c.decision == "violation" for c in self.checks)
            else "incomplete" if partial else "consistent"
        )
        if (
            self.status != ("partial" if partial else "complete")
            or self.outcome != outcome
        ):
            raise ValueError("result contradicts its checks or job outcome")
        return self

    @property
    def game_ref(self):
        return self.spec.game_ref


def _digest(value: Contract) -> str:
    # Absent extensions must not invalidate already retained v1 reports.
    def compatible(item):
        if isinstance(item, dict):
            extensions = {
                "accessCaseRef",
                "authorizationMatrix",
                "accessObservations",
                "collection",
                "observations",
            }
            return {
                k: compatible(v)
                for k, v in item.items()
                if k not in extensions or v is not None
            }
        if isinstance(item, list):
            return [compatible(v) for v in item]
        return item

    return "sha256:" + content_key(
        compatible(value.model_dump(mode="json", by_alias=True))
    )


def _display(value):
    if isinstance(value, str):
        return value
    value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(value) > 1000:
        return value[:800] + "... sha256:" + hashlib.sha256(value.encode()).hexdigest()
    return value


def _expected(spec, scenario):
    """Compute a serial outcome in the declared processing order, exactly once.

    Competing operations with equal processing times use the order in the
    scenario. Both legal serializations can be supplied as separate scenarios.
    Recognition time controls the event boundary; receipt time never implies
    that an operation was actually accepted by the target.
    """
    transitions = {(t.event, t.from_state): t.to_state for t in spec.transitions}

    def state_at(at, boundary=None):
        state = spec.initial_state
        for event in scenario.events:
            if event.at > at or (
                boundary
                and event.at == at
                and event.event == boundary.event
                and boundary.tie == "allow"
            ):
                break
            state = transitions[event.event, state]
        return state

    outcomes = {w.id: "unplaced" for w in scenario.wagers}
    wagers = {w.id: w for w in scenario.wagers}
    accepted, effects, decisions = {}, {}, {}
    balances = dict(scenario.initial_balances)
    for operation in scenario.operations:
        tx, kind = operation.transaction_id, operation.kind
        fingerprint = (kind, operation.wager_ref)
        if spec.authorization_matrix is not None:
            case = spec.authorization_matrix.case(operation.access_case_ref)
            # Access is checked before looking up a successful transaction:
            # knowing another account's transaction ID never authorizes replay.
            if case.expected == "deny":
                decisions[operation.id] = "denied"
                continue
        if tx in accepted:
            decisions[operation.id] = (
                "accepted" if accepted[tx] == fingerprint else "denied"
            )
            continue
        boundary = (
            spec.betting_close
            if kind == "wager"
            else spec.cashout_close if kind == "cashout" else None
        )
        at = (
            getattr(operation, boundary.recognition + "_at")
            if boundary
            else operation.processed_at
        )
        allowed = state_at(at, boundary) in spec.allowed_states[kind]
        if boundary:
            close = next(e.at for e in scenario.events if e.event == boundary.event)
            allowed &= at < close or (at == close and boundary.tie == "allow")
        wager = wagers.get(operation.wager_ref)
        if wager:
            allowed &= outcomes[wager.id] == (
                "unplaced" if kind == "wager" else "active"
            )
            if kind == "wager":
                allowed &= balances[wager.account_ref] >= wager.stake
            if kind == "rollback":
                allowed &= spec.interruption.policy == "rollback" and any(
                    e.event == spec.interruption.event and e.at <= at
                    for e in scenario.events
                )
        decisions[operation.id] = "accepted" if allowed else "denied"
        if not allowed:
            continue
        accepted[tx] = fingerprint
        if wager:
            amount = {
                "wager": -wager.stake,
                "cashout": wager.cashout_credit,
                "rollback": wager.stake,
                "settle": wager.settlement_credit,
            }[kind]
            if amount:
                effects[tx] = {
                    "transactionId": tx,
                    "kind": {"wager": "debit", "rollback": "rollback"}.get(
                        kind, "credit"
                    ),
                    "accountRef": wager.account_ref,
                    "amount": amount,
                }
                balances[wager.account_ref] += amount
            outcomes[wager.id] = {
                "wager": "active",
                "cashout": "cashed_out",
                "rollback": "rolled_back",
                "settle": "settled",
            }[kind]
    return decisions, effects, balances, outcomes, state_at(10**15)


def assess_rgs(
    spec: RgsScenarioSpec,
    traces: RgsTraceBundle,
    *,
    max_operations: int = MAX_OPERATIONS,
    max_seconds: float = 300,
    should_stop: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    on_progress: Callable[[RgsAssessment], None] | None = None,
    spec_input_ref: str | None = None,
    traces_input_ref: str | None = None,
    input_digests: dict | None = None,
) -> RgsAssessment:
    if (
        isinstance(max_operations, bool)
        or not isinstance(max_operations, int)
        or not 1 <= max_operations <= MAX_OPERATIONS
    ):
        raise ValueError("invalid operation budget")
    if not 0 < max_seconds <= 3600:
        raise ValueError("invalid time budget")
    scenario_ids = {s.id for s in spec.scenarios}
    if {t.scenario for t in traces.traces} - scenario_ids:
        raise ValueError("trace outside declared scenario scope")
    by_scenario = {t.scenario: t for t in traces.traces}
    for scenario in spec.scenarios:
        trace = by_scenario.get(scenario.id)
        if trace and {r.operation for r in trace.operations} - {
            o.id for o in scenario.operations
        }:
            raise ValueError("receipt outside declared operation scope")
        if trace and trace.access_observations:
            operations = {o.id: o for o in scenario.operations}
            for observation in trace.access_observations:
                operation = operations.get(observation.operation)
                if (
                    operation is None
                    or observation.case_ref != operation.access_case_ref
                ):
                    raise ValueError("access observation outside declared case scope")
    checks, stop_reason = [], None
    processed = 0
    started = clock()
    spec_ref, traces_ref = (
        spec_input_ref or spec.source_ref,
        traces_input_ref or traces.source_ref,
    )

    def result():
        complete = (
            processed == sum(len(s.operations) for s in spec.scenarios)
            and stop_reason is None
            and not any(c.decision == "incomplete" for c in checks)
            and len([c for c in checks if c.rule == "scenario-completion"])
            == len(spec.scenarios)
        )
        if spec.authorization_matrix is not None:
            complete &= len([c for c in checks if c.rule == "target-access"]) == sum(
                len(s.operations) for s in spec.scenarios
            )
        limitations = list(LIMITATIONS)
        if (
            traces.collection
            and traces.collection.get("producer") == "moonmind.security.rgs_target"
            and traces.observations
        ):
            limitations.remove("transport_not_exercised")
            if (
                spec.authorization_matrix
                and all(
                    c.decision != "incomplete"
                    for c in checks
                    if c.rule == "target-access"
                )
                and sum(c.rule == "target-access" for c in checks)
                == sum(len(s.operations) for s in spec.scenarios)
            ):
                limitations.remove("authorization_matrix_not_exercised")
        return RgsAssessment(
            spec=spec,
            spec_digest=_digest(spec),
            traces_digest=_digest(traces),
            traces_source_ref=traces.source_ref,
            spec_input_ref=spec_ref,
            traces_input_ref=traces_ref,
            input_digests=input_digests
            or {"spec": _digest(spec), "traces": _digest(traces)},
            retest=[
                "python",
                "-m",
                "moonmind.security.rgs_assessment",
                "--spec=" + spec_ref,
                "--traces=" + traces_ref,
                f"--max-operations={max_operations}",
                f"--max-seconds={max_seconds:g}",
                "--output=rgs-assessment.json",
            ],
            checks=list(checks),
            planned_operations=sum(len(s.operations) for s in spec.scenarios),
            completed_operations=sum(
                c.rule == "operation-decision" and c.decision != "incomplete"
                for c in checks
            ),
            status="complete" if complete else "partial",
            outcome=(
                "violations"
                if any(c.decision == "violation" for c in checks)
                else "consistent" if complete else "incomplete"
            ),
            stop_reason=stop_reason,
            limitations=[
                *limitations,
                *([stop_reason] if stop_reason else []),
                *(
                    ["target_" + traces.collection["stopReason"]]
                    if traces.collection and traces.collection.get("stopReason")
                    else []
                ),
            ],
        )

    def progress():
        if on_progress:
            on_progress(result())

    progress()
    for scenario in spec.scenarios:
        trace = by_scenario.get(scenario.id)
        refs = list(
            dict.fromkeys(
                [*scenario.evidence_refs, *(trace.evidence_refs if trace else [])]
            )
        )[:64]

        def check(
            rule,
            operation,
            expected,
            observed,
            *,
            incomplete=False,
            reconciled=False,
            scenario_id=scenario.id,
            evidence_refs=refs,
        ):
            checks.append(
                Check(
                    scenario=scenario_id,
                    operation=operation,
                    rule=rule,
                    expected=_display(expected),
                    observed=_display(observed),
                    decision=(
                        "incomplete"
                        if incomplete
                        else (
                            "consistent"
                            if expected == observed or reconciled
                            else "violation"
                        )
                    ),
                    evidence_refs=evidence_refs,
                )
            )

        decisions, effects, balances, outcomes, round_state = _expected(spec, scenario)
        receipts = {r.operation: r for r in trace.operations} if trace else {}
        operations = {o.id: o for o in scenario.operations}
        for operation in scenario.operations:
            stop_reason = (
                "cancelled"
                if should_stop()
                else (
                    "operation_budget"
                    if processed >= max_operations
                    else "time_budget" if clock() - started >= max_seconds else None
                )
            )
            if stop_reason:
                progress()
                return result()
            receipt = receipts.get(operation.id)
            observed = receipt.decision if receipt else "unavailable"
            reconciled = False
            if (
                receipt
                and receipt.decision == "unknown"
                and receipt.reconciled_by
                and trace.complete
            ):
                later = receipts.get(receipt.reconciled_by)
                retry = operations.get(receipt.reconciled_by)
                # A later accepted receipt must confirm the same financial
                # request. Wallet/audit reconciliation still runs independently.
                reconciled = bool(
                    later
                    and retry
                    and later.decision == decisions[operation.id] == "accepted"
                    and retry.transaction_id == operation.transaction_id
                    and (retry.kind, retry.wager_ref)
                    == (operation.kind, operation.wager_ref)
                    and list(operations).index(retry.id)
                    > list(operations).index(operation.id)
                )
            check(
                "operation-decision",
                operation.id,
                decisions[operation.id],
                observed,
                incomplete=observed in {"unknown", "unavailable"} and not reconciled,
                reconciled=reconciled,
            )
            processed += 1
            if spec.authorization_matrix is not None:
                case = spec.authorization_matrix.case(operation.access_case_ref)
                observations = (
                    {a.operation: a for a in trace.access_observations or []}
                    if trace
                    else {}
                )
                access = observations.get(operation.id)
                check(
                    "target-access",
                    operation.id,
                    case.expected,
                    access.observed if access else "unknown",
                    incomplete=access is None or access.observed == "unknown",
                    evidence_refs=list(
                        dict.fromkeys(
                            [*refs, *(access.evidence_refs if access else [])]
                        )
                    )[:64],
                )
            progress()
        final = bool(trace and trace.complete and set(receipts) == set(operations))
        check(
            "scenario-completion",
            scenario.id,
            "complete",
            "complete" if final else "partial",
            incomplete=not final,
        )
        for rule, rows in (
            ("wallet-effect", trace.wallet_effects if trace else []),
            ("audit-effect", trace.audit_effects if trace else []),
        ):
            grouped = defaultdict(list)
            for effect in rows:
                grouped[effect.transaction_id].append(
                    effect.model_dump(mode="json", by_alias=True, exclude={"id"})
                )
            for tx in sorted(set(effects) | set(grouped)):
                wanted = [effects[tx]] if tx in effects else []
                found = grouped.get(tx, [])
                # A partial capture cannot establish absence. Confirmed extra
                # or contradictory financial effects remain defects.
                incomplete = not final and (not found or found == wanted)
                check(rule, tx, wanted, found, incomplete=incomplete)
            ids = [e.id for e in rows]
            if len(ids) != len(set(ids)):
                check(
                    rule,
                    "duplicate-effect-id",
                    "unique effect identities",
                    "duplicate effect identity",
                )
        for account in sorted(set(balances) | set(trace.balances if trace else {})):
            check(
                "wallet-balance",
                account,
                balances.get(account, "unavailable"),
                trace.balances.get(account, "unavailable") if trace else "unavailable",
                incomplete=not final,
            )
        for wager in sorted(set(outcomes) | set(trace.wager_outcomes if trace else {})):
            check(
                "wager-outcome",
                wager,
                outcomes.get(wager, "unavailable"),
                (
                    trace.wager_outcomes.get(wager, "unavailable")
                    if trace
                    else "unavailable"
                ),
                incomplete=not final,
            )
        check(
            "round-state",
            scenario.id,
            round_state,
            trace.round_state if trace else "unavailable",
            incomplete=not final,
        )
        progress()
    stop_reason = (
        "cancelled"
        if should_stop()
        else "time_budget" if clock() - started >= max_seconds else None
    )
    if stop_reason:
        progress()
    return result()


def bind_job_result(
    payload: bytes,
    terminal_state: str,
    exit_code: int | None,
    input_refs: dict[str, str],
) -> bytes:
    report = RgsAssessment.model_validate_json(payload)
    data = report.model_dump(mode="json", by_alias=True)
    data["job"] = {
        "terminalState": terminal_state,
        "exitCode": exit_code,
        "inputRefs": input_refs,
    }
    if terminal_state != "succeeded" or exit_code != 0:
        data["status"] = "partial"
        if data["outcome"] != "violations":
            data["outcome"] = "incomplete"
        data["limitations"] = sorted({*data["limitations"], "job_did_not_succeed"})
    return RgsAssessment.model_validate(data).model_dump_json(by_alias=True).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m moonmind.security.rgs_assessment")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--traces", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-operations", type=int, default=MAX_OPERATIONS)
    parser.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    stopping = []
    previous = {
        number: signal.signal(number, lambda *_: stopping.append(True))
        for number in (signal.SIGTERM, signal.SIGINT)
    }

    def save(report):
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        staged = output.with_name(output.name + ".partial")
        staged.write_text(report.model_dump_json(by_alias=True))
        os.replace(staged, output)

    try:
        bodies = {}
        for name in ("spec", "traces"):
            with open(getattr(args, name), "rb") as source:
                bodies[name] = source.read(MAX_INPUT_BYTES + 1)
            if len(bodies[name]) > MAX_INPUT_BYTES:
                raise ValueError("input ceiling exceeded")
        spec = RgsScenarioSpec.model_validate_json(bodies["spec"])
        traces = RgsTraceBundle.model_validate_json(bodies["traces"])

        # An initial partial report exists before readiness is announced. Each
        # completed operation replaces it atomically, including on SIGKILL.
        def progress(report):
            save(report)
            if not getattr(progress, "announced", False):
                print("RGS assessment started", file=sys.stderr, flush=True)
                progress.announced = True

        report = assess_rgs(
            spec,
            traces,
            max_operations=args.max_operations,
            max_seconds=args.max_seconds,
            should_stop=lambda: bool(stopping),
            on_progress=progress,
            spec_input_ref=args.spec,
            traces_input_ref=args.traces,
            input_digests={
                k: "sha256:" + hashlib.sha256(v).hexdigest() for k, v in bodies.items()
            },
        )
        save(report)
        print(f"RGS assessment {report.status}: {report.outcome}", flush=True)
        return 0
    except (OSError, ValueError) as exc:
        # Input error messages can contain target data. Preserve a useful error
        # class without copying unvalidated contents into exported logs.
        print(f"invalid RGS input or output: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    sys.exit(main())
