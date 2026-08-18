# Omnigent Control-Plane Concurrency and Fencing

Status: Proposed design
Document Class: System / Feature Design View
Owners: MoonMind Platform
Last updated: 2026-08-18

**Issue:** [MoonLadderStudios/MoonMind#3704](https://github.com/MoonLadderStudios/MoonMind/issues/3704) ([Omnigent control plane 3/11]).

**Implementation tracking:** rollout notes and temporary handoffs belong under `docs/tmp/` or gitignored local-only artifacts, not in this canonical design document.

## Related docs

- [`docs/Omnigent/ControlPlaneAggregates.md`](./ControlPlaneAggregates.md) — the durable aggregates and repositories this document fences.
- [`docs/Omnigent/OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md) — the pure reducer that emits the `expected_revision` / `expected_fencing_generation` every guarded write validates.

## Why

The aggregates in ControlPlaneAggregates separated one provider session into
canonical session, turn attempts, observations, commands, and decisions. They
are still independently mutable from more than one process: a stale Temporal
activity result, a former host, a superseded session supervisor, an expired
lease owner, a delayed provider callback, or a janitor can each attempt a write.
Local locks and single-transaction boundaries do not protect a surface from a
writer in another process or from an activity result that arrives after ownership
changed. #3665 fixed exactly one lost-update race with a locked monotonic
compare-and-swap; this document generalizes that guarantee across every mutable
control-plane surface.

The invariant: **every lifecycle-changing write declares the exact revision and
ownership generation it observed, and a rejected stale write triggers a fresh
observation and reconciliation — never a blind retry of the same mutation.**

## Revision ownership

Every mutable aggregate carries its own monotonically increasing `revision`, and
each successful guarded write advances it by exactly one under a row lock
(`SELECT ... FOR UPDATE` on PostgreSQL; SQLite serializes writes):

| Aggregate | Revision column | Owned transitions |
| --- | --- | --- |
| `OmnigentSession` | `revision` | lifecycle, frontier, terminal, generation acquisition |
| `OmnigentTurnAttempt` | `revision` | delivery-state and attempt-terminal transitions |
| `OmnigentCommand` | `revision` | claim → delivery → applied/failed |
| `OmnigentCleanupAuthority` | `revision` | claim → complete |

A guarded update runs as a compare-and-swap: it loads the row for update,
validates the caller's expected authority against the locked row, applies the
change, and increments the revision. Zero effective rows (a mismatch) is stale
authority, returned as a typed conflict — not a silent overwrite. `expected_revision`
and the session-supervisor fencing generation are **mandatory** arguments on the
lifecycle-changing repository methods; application code never receives an
unconstrained mutable ORM entity.

## Fencing generations

Fencing generations are independent monotonic tokens, one per side-effect owner.
A newly acquired owner receives `current + 1` via
`SessionRepository.acquire_fencing_generation(scope, expected_revision=…)`, which
compare-and-swaps on the session revision so two racing replacements cannot both
win. After acquisition, every former owner of that scope is fenced out of
subsequent writes.

| `FencingScope` | Durable token | Fences |
| --- | --- | --- |
| `session_supervisor` | `OmnigentSession.fencing_generation` | session lifecycle, frontier, terminal, command execution |
| `provider_profile_lease` | `OmnigentSession.provider_profile_generation` | Provider Profile lease-owner writes and cleanup release |
| `host_lease` | `OmnigentSession.host_lease_generation` | host lease-owner writes and cleanup release |
| `cleanup` | `OmnigentCleanupAuthority.generation` | janitor claim/complete |

A lease-owner write declares the lease generation relevant to its side effect
(`expected_provider_profile_generation` / `expected_host_lease_generation`) in
addition to the mandatory session-supervisor generation; a superseded lease
generation is fenced (`fencing_conflict`) attributed to its own scope.

## Stable conflict outcomes

Every guarded operation resolves to a closed, low-cardinality
`ControlPlaneOutcome`. Reconcilers, telemetry, and callers branch on the outcome
instead of parsing messages or collapsing conflicts into a generic database
error.

| Outcome | Meaning | Treatment |
| --- | --- | --- |
| `applied` | write landed and advanced the revision | success |
| `already_applied` | effect already durably present (idempotent replay) | success, no-op |
| `revision_conflict` | expected revision ≠ current (lost update) | reload + reconcile |
| `fencing_conflict` | presented generation is superseded | reload + reconcile |
| `delivery_unknown` | provider side effect may already have occurred | reconcile, do not reissue |
| `immutable_authority_conflict` | conflicting identity / terminal authority | fail closed |
| `not_owner` | caller does not own the resource | fail closed |

A revision or fencing conflict is observable and actionable but is **not** an
execution failure: normal reconciliation reloads current authority and converges.
`immutable_authority_conflict` and `not_owner` are reserved fail-closed cases and
never silently regress durable authority. The CAS methods
(`compare_and_swap_session`, `compare_and_swap_turn`, `claim_command`,
`record_command_delivery`, `claim_cleanup`, `complete_cleanup`) return a
`CasResult` carrying the outcome and the current record; the convenience raising
wrappers (`update_lifecycle`, `mark_terminal`, `advance_state`) raise the
corresponding typed error for callers that prefer fail-closed exceptions.

## Command execution

A logical command carries command id, payload digest, expected session revision,
fencing generation, idempotency identity, and a low-cardinality `owner_class`
(never a high-cardinality identity). Execution is at-most-once:

1. `claim_command(owner_class)` compare-and-swaps `pending → claimed`. Exactly one
   caller receives `applied` (execution authority); concurrent activity retries of
   the same logical command receive `already_applied` and must reconcile from the
   recorded delivery state instead of executing the side effect again.
2. The owner performs the side effect, then `record_command_delivery(owner_class,
   outcome=…)` settles the command: `applied` confirms it; `delivery_unknown`
   parks it as delivery-ambiguous so the reconciler reconciles rather than blindly
   reissuing; a conflict fails it for retry against fresh authority. Only the
   claiming `owner_class` may record delivery.

Re-recording an identical logical command through `CommandRepository.record`
collapses to one journal row (duplicate-command suppression).

## Cleanup and janitor authority

Cleanup is durable, fenced authority in `OmnigentCleanupAuthority`, not a plain
mutable string. `claim_cleanup` compare-and-swaps `unclaimed → claimed`; exactly
one janitor wins and records the host / Provider Profile lease / provider-session
generations it is fenced against. `complete_cleanup` re-validates those recorded
generations against the live session before settling: if a replacement lease has
been acquired since the claim, completion is fenced (`fencing_conflict`) so a
former janitor cannot stop or release resources that now belong to a newer
generation. An **absent** cleanup-authority row means *unclaimed* (fail-closed
default), never "already cleaned / universally current"; nothing may complete a
cleanup it never claimed.

## Event and callback writes

`advance_observation_frontier` advances the durable provider-event / snapshot
frontier only when the caller proves it belongs to the current provider epoch
(the session-supervisor fencing generation). A delayed event or callback from a
superseded epoch is fenced and retained as an append-only observation without
regressing current lifecycle state (`stale_observation_retained` telemetry). A
valid historical event therefore remains auditable but never regresses authority.

## Crash-window behavior

Because each stage is an idempotent, revision-fenced compare-and-swap keyed by a
deterministic command identity, a crash or timeout at any window converges
without a duplicate logical side effect:

| Crash point | Recovery |
| --- | --- |
| before command claim | reconciler re-derives the same command id and claims it |
| after claim, before side effect | the owner re-runs; the claim is idempotent (`already_applied` to any other caller) |
| after side effect, before receipt persistence | `record_command_delivery(delivery_unknown)` parks it; reconciliation confirms via observation rather than reissuing |
| after receipt, before session transition | the fenced session transition is retried against current authority |
| after transition, before activity response | the transition is `already_applied`; the activity result is a no-op |

Delayed results converge the same way: a first activity attempt that completes
after a second acquired a newer generation is fenced; a host exit after
replacement-host readiness, a terminal event after a new provider epoch, and a
cleanup result after a linked continuation acquired new workspace authority are
each fenced or retained rather than allowed to regress current state.

## Telemetry

`moonmind.omnigent.control_plane.telemetry` emits bounded counters —
`revision_conflicts`, `fencing_conflicts`, `duplicate_command_suppressed`,
`delivery_unknown_reconciled`, `stale_observation_retained`,
`cleanup_claim_conflicts` — labeled only by `FencingScope` and a fixed metric
name. No workflow, session, turn, host, lease, profile, user, or credential
identity is ever used as a label, so a high-cardinality identity can never enter
the metric label space. Each event also emits a secret-free structured log line.

## Legacy / bounded migration policy

Columns are additive with fail-closed server defaults: existing `OmnigentCommand`
rows read back as `revision = 1`, `owner_class = NULL`; existing sessions carry
`fencing_generation = 0` and no cleanup-authority row. Absent explicit authority
is the fail-closed default (revision 1, generation 0, unclaimed cleanup) and is
**not** treated as universally-current authority: a write that does not match the
observed defaults still fails closed, and cleanup that was never claimed cannot be
completed.

## Out of scope

The Temporal-wired fenced command *executor* — the Activity/worker binding that
drives `claim_command → side effect → record_command_delivery` for each command
class — consumes these repository primitives and is delivered by the executor
rollout issue in the Omnigent control-plane series. This document defines the
durable authority, fencing contracts, and repository operations that executor
must use; it does not itself add workflow orchestration.
