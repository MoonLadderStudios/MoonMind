# Omnigent lifecycle reconciler and canonical transition contract

**Document Class:** Canonical declarative
**Status:** Accepted
**Owners:** MoonMind Platform
**Authority:** Owns the pure Omnigent session lifecycle transition contract — the
typed inputs, the closed decision vocabulary, the stable reason codes, the
evidence rules, and the authority boundary of
`moonmind.omnigent.reconciler.reconcile`. It does not own orchestration, side
effects, or the durable schema; those remain with the existing execution path
until the dedicated session workflow issue consumes this reducer.
**Traceability:** MoonLadderStudios/MoonMind#3702
([Omnigent control plane 1/11]); parent #3701; related escaped failures #3698,
#3683.

## 1. Purpose and authority boundary

Correctness for an Omnigent session was previously distributed across event
handlers, retry branches, snapshot probes, store mutations, host runtime code,
and terminal heuristics (for example the missed-terminal-edge snapshot poller in
`moonmind/omnigent/execute.py` from #3698 and the expanded terminal vocabulary
from #3683). Each new provider timing or retry ordering therefore required
another special case.

This contract defines **one** side-effect-free decision boundary:

```python
from moonmind.omnigent.reconciler import reconcile

decision = reconcile(
    intent=compiled_intent,       # CompiledSessionIntent
    durable=current_session_state,  # DurableSessionState
    observations=observation_set,   # ObservationSet
    now=deterministic_now,          # datetime supplied by the caller
)
```

`reconcile` is a pure function. It performs **no** database, network,
filesystem, Docker, artifact, logging, telemetry, or Temporal call, imports no
infrastructure module, and never mutates its inputs. It returns exactly one
`ReconciliationDecision`. Equal inputs always produce an equal decision. This
purity is enforced by an import-boundary test and an input-immutability test.

Transient provider events remain useful because they trigger *faster*
reconciliation, but they are never *required* for correctness: the same decision
can always be reached from durable state plus authoritative snapshots.

## 2. Domain objects (all versioned, `schemaVersion = "v1"`)

Every object is a strict pydantic model (`extra="forbid"`, camelCase wire
aliases, frozen). Unknown fields and unknown schema versions are rejected at
construction; `reconcile` additionally fails closed to
`quarantine_ambiguous_state` if a caller bypasses validation and passes an
unknown version.

- **`CompiledSessionIntent`** — compact domain view of the immutable execution
  contract needed for a decision (the full artifact-backed contract is owned by
  #3701). Its `provider` field is declarative and is never used as authority for
  a side effect.
- **`DurableSessionState`** — canonical identity and revision, owner token and
  fencing generation, desired lifecycle, provider-session attachment, current
  turn-attempt identity and submission state, Provider Profile and host lease
  state, last accepted cursor and snapshot digest, recorded terminal outcome and
  evidence, cleanup evidence, next deadline, and a bounded prior-decision
  summary. All authority values are durable.
- **`ObservationSet`** — independently sourced, timestamped observations:
  provider session snapshot, provider turn/transcript snapshot, normalized event
  frontier, host registration/runner state, Provider Profile and host lease
  state, workspace/checkpoint availability, artifact/terminal-evidence
  availability, and compatibility/runtime-readiness state. A field that is
  `None` means **not observed**; a present sub-observation with a negative flag
  means **observed negative**. That distinction is load-bearing.
- **`ReconciliationDecision`** — the closed decision `kind`, a stable
  `reason_code`, the `expected_revision` and `expected_fencing_generation` the
  command must be applied against, an optional `command` specification with a
  deterministic idempotency `command_id`, the bounded `next_deadline`, the
  `evidence_requirements`, `changes_product_visible_state`, and bounded
  `diagnostics`.

## 3. Decision vocabulary

The decision `kind` is a closed, versioned enum:

| Kind | Meaning |
| --- | --- |
| `no_op` | Session is fully settled; nothing to do. |
| `await_observation` | Wait for more authoritative observation; bounded deadline. |
| `ensure_profile_lease` | Acquire the Provider Profile lease. |
| `ensure_host` | Acquire/realize the host. |
| `ensure_provider_session` | Create/attach the provider session. |
| `submit_turn` | Submit the current turn attempt (at most once). |
| `record_provider_terminal` | Record a terminal that the provider reported explicitly. |
| `synthesize_terminal_from_snapshot` | Recover a missed terminal from snapshot/transcript evidence (#3698/#3683). |
| `harvest_evidence` | Collect terminal evidence/artifacts. |
| `begin_cleanup` | Start cleanup (distinct from task completion). |
| `release_leases` | Release leases last, after cleanup and once no consumer remains. |
| `retry_transient_observation` | An observation was transiently unavailable; retry it. |
| `quarantine_ambiguous_state` | Ambiguous/contradictory state; hold for external resolution. |
| `fail_nonretryable` | Unrecoverable; stop. |

Reason codes (`ReasonCode`) are a separate closed enum so the *why* of a decision
is stable and greppable independent of the *what*.

## 4. Evidence rules

- A provider event is an **observation**, not an unquestioned state mutation. A
  `running`/`active` observation only justifies `await_observation`.
- A lost terminal event is recoverable: an explicit terminal status with the
  terminal event observed on the frontier yields `record_provider_terminal`; a
  terminal status (or `idle` with a completed turn transcript) **without** the
  terminal event yields `synthesize_terminal_from_snapshot`.
- Provider `idle` alone is not terminal while a tool call remains open.
- Unknown provider status or compatibility vocabulary fails closed to
  `await_observation` or `quarantine_ambiguous_state`; it is never silently
  mapped to success.
- Terminal evidence must be harvested before cleanup, and cleanup must complete
  before leases are released.

## 5. Authority boundary and invariants

`expected_revision` and `expected_fencing_generation` on every decision come
from `DurableSessionState`, never from an observation or from intent, so command
execution cannot ignore concurrency authority. No decision trusts a
caller-supplied provider, host, lease, profile, endpoint, or workspace identity;
a command's `provider_session_id` is copied from durable authority only.

The reducer encodes and tests the twelve required invariants:

1. A provider event is an observation, not a state mutation.
2. A lost terminal event is recoverable from snapshot/transcript evidence.
3. `idle` alone is not terminal when a tool call is open.
4. Attempt terminality is distinct from canonical session terminality.
5. A stale/contradictory observation cannot move a terminal session backward (a
   late `running` observation is ignored; two different terminal outcomes
   quarantine).
6. Unknown provider status or compatibility vocabulary fails closed.
7. A logical command is not reissued when it is durably accepted or delivery is
   ambiguous; `submit_turn` carries a deterministic per-attempt `command_id`.
8. Leases are not released while a credential or host consumer is still observed
   or durably owned.
9. Cleanup completion is distinct from task completion and never erases the
   recorded terminal outcome or evidence.
10. Every nonterminal decision carries a bounded next deadline; only `no_op`,
    `quarantine_ambiguous_state`, and `fail_nonretryable` are settled.
11. No decision trusts caller-supplied identity for authority.
12. Reconciliation is deterministic for equal inputs.

## 6. Diagnostic confidentiality

`DecisionDiagnostics` carries only enum codes, booleans, and observation *kind
names*. It never includes any workflow, user, provider-session, host, profile,
credential, or workspace secret. This is asserted by test.

## 7. Shadow mode

`shadow_compare(legacy_action, decision)` maps the legacy execution path's action
vocabulary onto the decision vocabulary and returns a bounded, non-sensitive
`ShadowComparison` (agreement plus a divergence reason). It lets the reducer run
alongside the existing path so a bounded legacy-vs-reconciler comparison can be
logged or persisted, **without** the reducer becoming a second orchestration
source of truth: it only compares, it never acts.

## 8. Non-goals

The reconciler owns the pure decision boundary only. The following remain owned
elsewhere and are outside this contract's authority:

- orchestration and durable scheduling (the Temporal session workflow);
- performing provider, database, Docker, or artifact side effects;
- owning or migrating the durable session schema;
- replacing native Omnigent UI behavior.

Transient provider events feed the reducer to trigger faster reconciliation but
are never required for correctness; the same decision is always reachable from
durable state plus authoritative observations.
