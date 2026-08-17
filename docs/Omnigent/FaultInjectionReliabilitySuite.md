# Omnigent fault-injection reliability suite

*Issue: [MoonLadderStudios/MoonMind#3709](https://github.com/MoonLadderStudios/MoonMind/issues/3709)
(Omnigent control plane 8/11).*

This document describes the desired state of MoonMind's programmable
fault-injection provider and model-based reliability suite. The implementation
lives in `moonmind/omnigent/faultkit/` and is runtime-neutral, hermetic (no
network, no credentials, no Docker socket), and secret-free by construction.

## Why

The escaped-failure replay corpus, deterministic conformance fixtures, and
focused fake-server tests grew reactively around specific incidents. They do not
express one stateful fault model that can systematically generate *unseen*
interleavings (dropped, duplicated, delayed, reordered, ambiguous, and
contradictory provider / host / lease / activity / cleanup behavior). The
faultkit provides that single model. Every escaped incident becomes a generalized
invariant plus a minimized declarative scenario in this framework rather than
another bespoke test branch.

## Components

| Module | Responsibility |
| --- | --- |
| `scenario` | The versioned declarative `moonmind.omnigent-fault-scenario/v1` format, its loader (dict / YAML / JSON), and the compatibility policy for unknown schema versions (fail, or quarantine and refuse to execute). |
| `commands` | The logical command vocabulary and the five shared fail-before / fail-after command windows. |
| `recording` | An independent recorder of requests, side effects, logical idempotency keys, payload digests, responses, and observations — with secret scrubbing. |
| `fake_provider` | The programmable, recording fake Omnigent provider/host driven by a scenario. |
| `injectors` | Host, Provider-Profile lease, and infrastructure fault controls plus command-window crash injection. |
| `reference_model` | A compact reference state machine, independent of the production reconciler, used as the invariant oracle. |
| `reconciler` | The reconciler-under-test: MoonMind's reconciliation policy. |
| `invariants` | The twelve named reliability properties. |
| `generator` | A seeded, deterministic action-sequence generator. |
| `minimizer` | A delta-debug minimizer that shrinks a failing sequence while preserving the violated invariant. |
| `harness` | Drives reconciler + fake provider + reference model over a scenario and returns a `RunResult`. |
| `diagnostics` | Builds a safe, credential-free diagnostic bundle. |
| `corpus` | The initial declarative scenarios lifted from escaped incidents and the incident-ingestion contract. |
| `ci_policy` | The bounded PR-CI corpus, the fixed reliability corpus, the rotating main corpus, and the predictability budgets. |

## Declarative scenario format

```yaml
schemaVersion: moonmind.omnigent-fault-scenario/v1
seed: 12345
steps:
  - on: ensure_session
    sideEffect: created
    response: success
  - on: submit_turn
    sideEffect: accepted
    response: drop            # side effect commits, receipt is lost
  - on: read_events
    emit:
      - type: turn.running
    disconnect: true
  - on: observe_snapshot
    return:
      sessionState: idle
      turnState: completed
      unfinishedToolCalls: 0
  - on: read_events
    emit: []
```

A step may also declare `crashAt` (one of the five command windows), `fault` (a
named host/lease/infrastructure fault), `generation` (for fencing), `duplicate`,
`reorder`, `heartbeat`, `latencyMs`, and `turn` (a logical turn identity).

Unknown schema versions **fail fast** by default, or are **quarantined** and
refused execution when `quarantine=True` is requested.

## Command windows

Every logical command executes through five phases so a crash can be injected at
each boundary and eventual safe convergence verified:

```
before_claim
after_claim_before_side_effect
after_side_effect_before_receipt
after_receipt_before_state_transition
after_transition_before_activity_response
```

## Reference model

The reference model derives the correct convergent state from the provider's
authoritative ground truth (durable side effects + the latest observed
snapshot). It knows only the allowed transitions and never imports or calls the
production reconciler, so a divergence is evidence of a real defect.

## The twelve invariants

1. **At-most-once logical submission** — one turn idempotency identity produces
   at most one accepted provider side effect.
2. **Eventual convergence** — when the provider is observable and evidence is
   sufficient, MoonMind reaches the correct terminal/active state despite loss.
3. **Monotonic authority** — durable session and turn revisions never move
   backward.
4. **Fencing safety** — a former generation never mutates current authority.
5. **No blind ambiguity retry** — a lost response after a side effect produces
   reconciliation, not an unbounded duplicate command.
6. **Distinct terminality** — turn / session / AgentRun / Workflow / cleanup /
   remediation terminal states are not conflated.
7. **Lease safety** — Provider-Profile capacity is not released while a
   credential consumer remains.
8. **Cleanup safety** — cleanup never deletes replacement-generation resources.
9. **Historical-read safety** — terminal evidence remains available after
   live-resource removal.
10. **Compatibility safety** — unknown provider/scenario schema versions fail or
    quarantine per declared policy.
11. **Secret safety** — retained fault evidence and minimized scenarios contain
    no raw credentials or production secrets.
12. **Deterministic replay** — a seed and scenario produce the same decisions and
    observations; a non-deterministic scenario is itself a failure.

## Incident ingestion contract

Each escaped incident adds a `corpus.IncidentScenario` (also serialized under
`tests/integration/reliability/replays/omnigent-fault-*/`) carrying: a stable
generalized invariant, a minimized declarative scenario, the source incident/PR
reference, the expected decision and classification, any workspace/artifact
fixture, and the operational signal that would detect the failure class.

The required initial scenarios are seeded from #3698 (missed terminal edge),
#3683 (idle completion vocabulary), #3665 (stale state rollback), #3684
(remediation authority loss), #3694 (image authority drift), #3697 (WebSocket
missing), #3696/#3685 (duplicate binding), plus first-message-response-lost,
cleanup-racing-continuation, and lease-replacement-while-old-host-alive.

## Test layers

* **Pure domain** — thousands of generated transition sequences run quickly
  against the reconciler and reference model (`tests/unit/omnigent/faultkit/`).
* **Repository / concurrency, Temporal, API / browser, exact-image** — the same
  declarative scenarios are the reusable driver for the heavier layers. The
  `harness.RunResult` and `Scenario` objects are the stable contract those layers
  consume; each layer replays a bounded, representative subset through its real
  boundary.

The hermetic reliability journey
(`tests/integration/reliability/test_omnigent_fault_model_journey.py`) covers the
pure-domain, incident-corpus, and diagnostic boundaries without a Temporal
server, database, credentials, or network.

## CI policy

* Fast generated domain tests and the fixed incident corpus run in required PR CI
  (`ci_policy.PR_CI_SEEDS`, bounded and deterministic).
* A fixed seed corpus runs in the required reliability journey
  (`ci_policy.FIXED_RELIABILITY_SEEDS`).
* A larger rotating seed corpus (`ci_policy.rotating_seeds`) runs on main or a
  schedule.
* Every failure prints and uploads a diagnostic bundle: the seed, minimized
  scenario, decision journal, provider request log, and safe diagnostic refs.
* Flaky retry is never a passing result; a non-deterministic scenario is a
  failure (the `deterministic_replay` invariant). Explicit time and
  scenario-count budgets keep the suite predictable.

## Non-goals

* Claiming a fake-provider pass equals protected-live stock-provider acceptance.
* Fuzzing unbounded raw network bytes without a lifecycle model.
* Retaining raw production transcripts or secrets in regression fixtures.
