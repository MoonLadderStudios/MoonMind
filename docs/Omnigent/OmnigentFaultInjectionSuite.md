# Omnigent fault-injection provider and model-based reliability suite

Source issue: MoonLadderStudios/MoonMind#3709
([Omnigent control plane 8/11]).

This document describes the desired state of the programmable fault-injection
provider and the model-based reliability suite that proves Omnigent lifecycle
invariants under dropped, duplicated, delayed, reordered, ambiguous, and
contradictory provider, host, lease, activity, and cleanup behavior.

The framework lives in `moonmind/omnigent/faultlab/` and is deliberately
side-effect-free: no database, network, filesystem, Docker, artifact, logging,
telemetry, or Temporal dependency. That is what lets one scenario be replayed
from unit, reliability-journey, and (future) Temporal / API / browser layers
without change.

## Components

| Module | Responsibility |
| --- | --- |
| `scenario.py` | The versioned declarative fault-scenario format (`moonmind.omnigent-fault-scenario/v1`), its loader, and the unknown-version fail/quarantine policy. |
| `provider.py` | The programmable fake provider and its **independent** side-effect / idempotency ledger (records every request, logical idempotency key, payload digest, side effect, response, observation). |
| `reference_model.py` | A compact reference state machine written independently of the production reducer; the second oracle a run is compared against. |
| `harness.py` | The execution harness: plays a `FaultPlan` against the production `reconcile` reducer with transport, observation, and crash faults; produces a decision journal and cross-checks the reference model. |
| `invariants.py` | The twelve required reliability invariants as checkable predicates over a trace. |
| `generator.py` | The seed-based deterministic fault-plan generator and the determinism check. |
| `minimizer.py` | The greedy delta-debugging minimizer that reduces a failing plan while preserving its invariant failure. |
| `conversions.py` | Total, round-trippable conversion between the executable `FaultPlan` and the declarative `FaultScenario`. |
| `diagnostics.py` | Secret-safe diagnostic bundles (seed, minimized scenario, decision journal, provider request log, safe refs). |
| `corpus.py` | The initial generalized escaped-incident scenarios and the incident-ingestion workflow. |
| `scenarios/*/fault-scenario.yaml` | The packaged declarative corpus. |

## The declarative fault-scenario format

A scenario is versioned and seed-based. Unknown schema versions **fail or
quarantine according to declared policy** — never silently execute:

```yaml
schemaVersion: moonmind.omnigent-fault-scenario/v1
seed: 12345
groundTruthTerminal: success
recoveryRound: 4
steps:
  - on: submit_turn
    sideEffect: accepted
    response: drop
  - on: read_events
    emit:
      - type: turn.running
    disconnect: true
  - on: observe_snapshot
    return:
      sessionState: idle
      turnState: completed
      unfinishedToolCalls: 0
```

`recoveryRound` bounds the fault window: after it, the world reports the ground
truth honestly. This is what makes **eventual convergence** a decidable property
rather than an open-ended wait.

## The twelve invariants

Generated tests enforce, at minimum:

1. **At-most-once logical submission** — one turn idempotency identity performs
   at most one accepted provider side effect (asserted from the independent
   ledger, not from MoonMind state).
2. **Eventual convergence** — within the bounded fault window the session reaches
   the correct terminal or active state despite event loss.
3. **Monotonic authority** — durable session/turn revisions and the derived
   lifecycle phase never move backward.
4. **Fencing safety** — a former generation never mutates current session, host,
   workspace, lease, or cleanup authority.
5. **No blind ambiguity retry** — a lost response after a side effect produces
   reconciliation, not an unbounded duplicate command.
6. **Distinct terminality** — turn, session, AgentRun, Workflow, cleanup, and
   remediation terminal states are not conflated.
7. **Lease safety** — Provider Profile capacity is not released while a credential
   consumer remains.
8. **Cleanup safety** — cleanup never deletes replacement-generation resources.
9. **Historical-read safety** — terminal evidence and diagnostic projection remain
   available after live-resource removal.
10. **Compatibility safety** — unknown provider or scenario schema versions fail
    or quarantine.
11. **Secret safety** — retained fault evidence and minimized scenarios contain no
    raw credentials or secrets (bundles are digest-only and secret-scanned).
12. **Deterministic replay** — a seed and scenario produce the same decisions and
    observations; a nondeterministic scenario is itself a failure.

## Reference model independence

The reference state machine re-derives the legal lifecycle ordering by hand and
never imports or calls the production reducer. Each generated run feeds the
reconciler's distinct emitted commands into the reference model; an illegal
transition there means the reconciler emitted an out-of-order, duplicated, or
post-terminal command. Comparing two independently written models is what gives
the suite its confidence — a shared bug would have to exist in both.

## Crash windows

Every logical command can be interrupted at each of the five shared windows:

```
before_claim
after_claim_before_side_effect
after_side_effect_before_receipt
after_receipt_before_state_transition
after_transition_before_activity_response
```

A crash at a side-effect-only window performs the provider side effect but records
no durable transition, so the retry must dedup via the idempotency key — this is
where at-most-once is proven against real interruption, not just against clean
retries.

## Incident ingestion workflow

Every escaped reliability incident adds: a stable generalized invariant, a
minimized declarative fault scenario, the source incident/PR reference, the
expected outcome, any fixture needed for hermetic replay, and an operational
signal that would detect the failure class. `corpus.ingest_incident` minimizes a
failing plan and returns the safe, bounded scenario to store under the reliability
replay corpus.

## Test layers and CI policy

- **Pure domain** (required PR CI): `tests/unit/omnigent/faultlab/` runs hundreds
  of generated interleavings against the reducer and reference model.
- **Reliability journey** (required CI, `integration_ci` + `reliability_journey`):
  `tests/integration/reliability/test_omnigent_fault_corpus.py` runs the fixed
  declarative corpus and a fixed deterministic seed corpus. Fully hermetic — no
  network, credentials, Docker, or Temporal server.
- **Rotating seed coverage** (main/schedule): the generated corpus scaled to a
  larger seed range.
- **Repository/concurrency, Temporal, API/browser, and exact-image layers**
  consume the same scenarios and provider ledger. The framework is intentionally
  layer-neutral so those bindings are thin adapters; they are tracked as follow-up
  bindings on top of this substrate and are not required for the pure-domain and
  reliability-journey gates above.

Every failure prints and can upload a reproduction-complete, secret-safe
diagnostic bundle (seed, minimized scenario, decision journal, provider request
log, safe refs). Flaky retry is never a passing result: determinism is asserted
directly.

## Non-goals

- Claiming a fake-provider pass is equivalent to protected-live stock-provider
  acceptance.
- Fuzzing unbounded raw network bytes without a lifecycle model.
- Retaining raw production transcripts or secrets in regression fixtures.
