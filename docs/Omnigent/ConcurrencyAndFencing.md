# Omnigent Control-Plane Concurrency and Fencing

**Document Class:** Canonical declarative
**Status:** Current
**Owners:** MoonMind Platform
**Authority:** Canonical MoonMind contract for optimistic concurrency, lease
fencing, command idempotency, cleanup authority, and delayed-result reconciliation
across the Omnigent control plane.

Source issue: [MoonLadderStudios/MoonMind#3704](https://github.com/MoonLadderStudios/MoonMind/issues/3704)
(control plane 3/11). Builds on the locked monotonic revision boundary introduced
by #3665.

Implementation progress belongs in the roadmap, issues, and pull requests. This
document defines the durable desired state.

## Related documents

- [`docs/Omnigent/OmnigentAdapter.md`](./OmnigentAdapter.md)
- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)
- [`docs/Omnigent/OmnigentHostOAuth.md`](./OmnigentHostOAuth.md)

---

## 1. Purpose

Optimistic concurrency and lease fencing are **universal** across the Omnigent
control plane. A stale activity, a former worker, a superseded host, an expired
lease owner, a delayed callback, or a janitor can never overwrite newer
authority.

Every mutable lifecycle command declares the exact revision and ownership
generation it observed. A rejected stale write triggers a fresh observation and
reconciliation, never a blind retry of the same mutation.

Local locks and single-transaction boundaries are not sufficient: they do not
protect a surface from a writer in another process or from an activity result
that arrives after ownership changed. Durable revisions and fencing generations
do.

## 2. Revisions

Every mutable aggregate carries a monotonically increasing `revision`. A
lifecycle-changing write applies only when the caller's observed revision (and
the relevant fencing generation) still match:

```sql
UPDATE ...
SET ..., revision = revision + 1
WHERE id = :id
  AND revision = :expected_revision
  AND fencing_generation = :expected_generation;
```

Zero updated rows means stale authority. The write returns a typed conflict and
the caller reconciles from current state.

Revision-bearing aggregates:

| Aggregate | Table | Revision authority |
| --- | --- | --- |
| Canonical session state | `omnigent_bridge_sessions` | `revision` + `supervisor_generation` |
| Turn-attempt submission/terminal state | `omnigent_turn_attempts` | `revision` + `fencing_generation` |
| Logical command | `omnigent_commands` | `revision` |
| Cleanup authority | `omnigent_cleanup_authority` | `revision` + `claim_generation` |
| Host / Provider Profile lease | `omnigent_oauth_host_leases`, `managed_agent_provider_profiles` | `credential_generation`, `host_auth_generation` (pre-existing #3665 baseline) |

## 3. Fencing generations

A fencing generation is a strictly increasing token owned by exactly one
side-effect owner. `omnigent_fencing_generations` is the universal registry:
acquiring a scope returns a strictly newer generation, after which the prior
owner cannot mutate provider, host, workspace, cleanup, or durable state fenced
by that scope.

Fencing scopes (by `scope_kind`):

- `session_supervisor` — the session supervisor generation.
- `host_replacement` — host lease and host replacement generation.
- `provider_epoch` — provider-session epoch / attachment generation.
- `workspace_publication` — workspace publication generation.
- `cleanup` — cleanup / janitor generation.

A scope key names one concrete owner (for example
`session_supervisor:<bridge_session_id>`). High-cardinality identity stays in the
scope key and durable rows, never in a metric label.

## 4. Command execution

Every logical command in `omnigent_commands` carries:

- command id and payload digest;
- expected session and turn revisions;
- the relevant fencing generations;
- an idempotency identity (`idempotency_key`, unique);
- a low-cardinality owner identity class (`owner_class`);
- delivery state and provider receipt when available.

Command handlers validate fencing **before** starting the side effect and again
**before** committing a result that could alter current authority. A retry of the
same logical command (same idempotency key) converges on the single durable
claim; a duplicate with a matching payload digest is suppressed. A reused
idempotency identity carrying a different payload is an immutable-authority
conflict.

When a provider side effect may already have occurred, the handler records
`delivery_state = "delivery_unknown"` and reconciles, rather than blindly issuing
a second command.

## 5. Event and callback writes

Delayed events and callbacks resolve enough authority to prove their session and
provider epoch. A valid historical event is retained as an **observation**
(`omnigent_turn_attempts.observation_frontier`) but may never regress current
lifecycle state. Advancing the observation frontier is itself a revision-checked
write; an event at or below the frontier is retained without regression.

## 6. Cleanup and janitor

Cleanup is fenced against the host, provider-session, workspace, and lease
generations current when it was claimed. Exactly one janitor holds cleanup
authority for a session:

- a replacement janitor with a strictly newer generation fences the prior owner;
- a janitor with an equal or older generation loses with a cleanup-claim
  conflict;
- a former janitor whose generation was superseded cannot complete cleanup or
  release resources that now belong to a replacement generation;
- completed cleanup is idempotent.

A janitor may complete abandoned cleanup only from durable cleanup authority.

## 7. Repository API

`moonmind.omnigent.concurrency.OmnigentControlPlaneRepository` is the typed
boundary. Application code never receives unconstrained mutable SQLAlchemy
entities: reads return immutable snapshots and writes return typed results.
Expected revision and fencing arguments are mandatory on lifecycle-changing
methods.

| Method | Responsibility |
| --- | --- |
| `load_for_update(aggregate, key)` | Locked, immutable observation snapshot |
| `compare_and_swap_session(...)` | Advance session revision under supervisor fencing |
| `compare_and_swap_turn(...)` | Advance turn-attempt revision under fencing |
| `advance_observation_frontier(...)` | Retain delayed events without regression |
| `acquire_fencing_generation(scope_key, scope_kind)` | Return a strictly newer generation |
| `current_fencing_generation(scope_key)` | Observe the current generation |
| `claim_command(...)` | Claim one logical command exactly once |
| `record_command_delivery(...)` | Persist delivery state / receipt / delivery ambiguity |
| `claim_cleanup(...)` | Claim durable cleanup authority |
| `complete_cleanup(...)` | Complete cleanup from current authority only |

## 8. Conflict outcomes

`ConflictOutcome` is the stable vocabulary. A conflict is observable and
actionable but is not treated as an execution failure when normal reconciliation
can safely converge.

| Outcome | Meaning |
| --- | --- |
| `applied` | The write applied and advanced the revision |
| `already_applied` | The intended state already holds (idempotent) |
| `revision_conflict` | Observed revision was stale; re-observe and reconcile |
| `fencing_conflict` | Observed fencing generation was superseded |
| `delivery_unknown` | Provider side effect ambiguity recorded for reconciliation |
| `immutable_authority_conflict` | Attempt to regress newer terminal / immutable authority |
| `not_owner` | Caller is not the current owner of the fenced resource |

## 9. Crash-window behavior

For each command class the model converges without a duplicate logical side
effect across every crash/timeout window:

1. **before command claim** — nothing durable changed; a retry claims cleanly.
2. **after claim, before side effect** — the claim is durable; the same owner
   resumes the side effect; another owner observes the claim and does not
   duplicate it.
3. **after side effect, before receipt persistence** — the command is marked
   `delivery_unknown`; reconciliation resolves it to `delivered`/`reconciled`
   rather than re-issuing.
4. **after receipt persistence, before session transition** — the receipt is
   durable; the session transition re-applies under compare-and-swap.
5. **after transition, before activity response** — the transition already
   advanced the revision; a retried activity observes `already_applied`.

Delayed results converge the same way: a first attempt completing after a second
attempt acquired a newer generation is fenced out; a host exit arriving after
replacement-host readiness, or a terminal event arriving after a new provider
epoch, is retained as an observation but cannot regress current state; a cleanup
result arriving after a linked continuation acquired new workspace authority is
rejected as `not_owner`.

## 10. Legacy compatibility

Rows created before this model carry `revision = 1` and `*_generation = 1` (the
column defaults and `>= 1` check constraints). They participate in
compare-and-swap immediately and are never treated as universally current
authority: a caller that has not observed the current revision receives a
`revision_conflict` and reconciles.

## 11. Telemetry

`record_concurrency_event` emits bounded counters (StatsD
`omnigent.control_plane.events`) and structured logs for:

- revision conflicts;
- fencing conflicts;
- immutable-authority conflicts and not-owner rejections;
- duplicate-command suppression;
- delivery-unknown reconciliation;
- stale observation retention;
- cleanup claim conflicts.

Labels are restricted to the bounded `event` and `surface` dimensions. Workflow,
session, turn, host, lease, profile, user, and credential identity — and any
secret-shaped value — never enter a metric label.
