# Omnigent Lifecycle Reconciler

Source issue: MoonLadderStudios/MoonMind#3702 ([Omnigent control plane 1/11]).

This document is the canonical, declarative description of the pure Omnigent
lifecycle reconciler: its transition vocabulary, the evidence rules that gate
terminal recovery and cleanup, and the authority boundary it enforces. The
implementation lives under `moonmind/omnigent/reconciler/`.

## Purpose and authority boundary

The reconciler is **one side-effect-free decision boundary** for the Omnigent
session lifecycle. It converts three immutable inputs into a single explicit
decision:

```python
decision = reconcile(
    intent=compiled_intent,       # CompiledSessionIntent
    durable=current_session_state, # DurableSessionState
    observations=observation_set,  # ObservationSet
    now=deterministic_now,         # datetime supplied by the caller
)
```

`reconcile()` performs **no** database, network, filesystem, Docker, artifact,
logging, telemetry, or Temporal calls. It never reads the clock (`now` is an
input) and never uses randomness, so it is deterministic for equal inputs. A
purity test (`tests/unit/omnigent/reconciler/test_purity.py`) statically
enforces this by scanning the package's imports and call sites.

Authority rules:

- **Durable state is authoritative.** Every identity the reducer trusts
  (canonical session id, provider-session attachment, owner token, fencing
  generation, lease ownership) comes from `DurableSessionState`.
- **Observations are evidence, never authority.** A provider event or snapshot
  can corroborate or contradict durable state, but it cannot replace it. An
  observation whose provider-session identity contradicts durable state
  quarantines rather than being adopted.
- **The reducer decides; it does not act.** Each decision carries the expected
  durable revision and fencing generation so a downstream executor applies the
  side effect under optimistic concurrency. Orchestration and side effects stay
  in existing code until a later control-plane issue consumes the reconciler.

## Domain inputs

All domain objects are versioned (`schema_version`) and reject unknown envelope
versions at construction (`UnknownSchemaVersionError`). Frozen dataclasses reject
unknown fields structurally.

- **`CompiledSessionIntent`** — a compact domain view of the immutable execution
  contract needed for reconciliation (identity, provider, agent, attempt budget,
  reconcile cadence, cleanup requirement). The full artifact-backed
  execution-intent contract is owned by the typed-intent issue (#3701).
- **`DurableSessionState`** — canonical identity and revision; owner and fencing
  generation; desired lifecycle; durable phase; provider-session attachment;
  current turn-attempt identity and submission state; host / Provider Profile
  lease state; last accepted cursor and snapshot digest; terminal and cleanup
  evidence; reconciliation deadline and prior decision summary.
- **`ObservationSet`** — independently-sourced, timestamped observations: provider
  session snapshot, provider turn/transcript snapshot, normalized event frontier,
  host registration/runner state, lease state, workspace/checkpoint availability,
  artifact/terminal-evidence availability, and compatibility/runtime readiness.

An **absent** observation (never fetched this cycle) is distinguishable from an
**observed negative** (source reached, reported unavailable/errored) via
`ObservationPresence` (`ABSENT` / `PRESENT` / `NEGATIVE`).

## Decision vocabulary

`ReconciliationDecision.action` is drawn from a closed, versioned vocabulary:

| Action | Meaning |
| --- | --- |
| `no_op` | Nothing to do (also the terminal state once closed/failed). |
| `await_observation` | Wait for more evidence before deciding; bounded deadline. |
| `ensure_profile_lease` | Acquire the Provider Profile lease. |
| `ensure_host` | Register / ready the host. |
| `ensure_provider_session` | Open the durable provider session. |
| `submit_turn` | Submit the current turn attempt (product-visible). |
| `record_provider_terminal` | Record a terminal observed from a provider event. |
| `synthesize_terminal_from_snapshot` | Recover a lost terminal edge from snapshot + transcript evidence. |
| `harvest_evidence` | Harvest terminal/artifact evidence before cleanup. |
| `begin_cleanup` | Start cleanup after evidence is harvested. |
| `release_leases` | Release host / profile leases (only when no consumer remains). |
| `retry_transient_observation` | Re-attempt a transiently unavailable observation. |
| `quarantine_ambiguous_state` | Hand an ambiguous/contradictory state to external authority. |
| `fail_nonretryable` | Terminal, non-retryable failure. |

Each decision also carries: stable **reason codes**; the **expected revision**
and **fencing generation**; a deterministic **command spec** (`kind` +
`command_id` + bounded, non-secret parameters) when a side effect is needed; a
**next reconciliation deadline** or explicit **wait authority**; declared
**evidence requirements**; and a **`changes_product_visible_state`** flag.

Reason codes are a closed, versioned vocabulary (`REASON_CODE_VERSION`). They are
never parsed from untrusted input at runtime; `parse_reason_code` enforces the
fail policy when a persisted code is read back.

## Lifecycle ladder

The durable phase advances monotonically along:

```
pending -> profile_leased -> host_ready -> provider_session_open
        -> turn_in_flight -> terminal_recorded -> evidence_harvested
        -> cleanup_started -> leases_released -> closed
```

`quarantined` and `failed` are off-ladder holding states. A stale or
contradictory observation can never move the durable phase backward.

## Evidence rules

- A **provider event is an observation**, not an unquestioned mutation. A
  terminal event yields a *decision to record* under concurrency control.
- A **lost terminal edge** is recovered from sufficient authoritative snapshot
  and transcript evidence: no terminal event on the frontier, provider session
  snapshot terminal, response recorded, and no open tool call →
  `synthesize_terminal_from_snapshot` (reproduces #3698).
- **Provider `idle` alone is not terminal.** Idle with an active/pending tool
  call defers (`await_observation`). Idle after completed, recorded work with no
  open tool call is treated as `completed` (reproduces #3683).
- **Attempt terminality is distinct from session terminality.** A confirmed
  attempt failure with retries remaining submits a *new* attempt rather than
  sealing the canonical session.
- **Unknown provider status or compatibility vocabulary fails closed** to
  quarantine; it is never silently mapped to success.
- **At-most-once logical submission.** A durably-known accepted submission is not
  re-issued; an ambiguous (sent-but-unconfirmed) submission waits for a snapshot
  rather than being resent.
- **Leases are not released while any consumer is observed or durably owned.**
- **Cleanup completion is distinct from task completion** and cannot erase
  durable reads or evidence: evidence is harvested before cleanup begins.

## Invariants

The reducer encodes and tests the twelve invariants from the issue
(`tests/unit/omnigent/reconciler/test_reconcile_invariants.py`), and every
nonterminal decision bounds its next step with a deadline or explicit wait
authority (`ReconciliationDecision.__post_init__`).

## Shadow mode

`compare_shadow()` lets the reconciler run alongside the existing execution path
and compares its decision to the legacy action label, producing a bounded,
credential-free `ShadowComparison` record. It **never executes, mutates, or
persists** anything, so the reconciler does not become a second orchestration
source of truth. `ShadowComparison.to_log_dict()` returns only decision
vocabulary, reason codes, the canonical session id, and a truncated note — never
provider-session, host, profile, lease, credential, workspace, or user identity.

Unknown legacy action labels surface as an explicit divergence
(`legacy_recognized=False`), never a silent agreement.

## Compatibility policy

MoonMind is pre-release: the reconciler supports exactly one version of each
contract. When a contract changes, add the new version and update every caller in
the same change — do not add aliases or translation layers. Unknown envelope
versions fail; unknown runtime *values* (provider status, compatibility token)
quarantine. Both policies are covered by
`tests/unit/omnigent/reconciler/test_versions_and_contracts.py`.
