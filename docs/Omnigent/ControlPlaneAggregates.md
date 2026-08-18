# Omnigent Control-Plane Aggregates

Status: Proposed design
Document Class: System / Feature Design View
Owners: MoonMind Platform
Last updated: 2026-08-18

**Issue:** [MoonLadderStudios/MoonMind#3703](https://github.com/MoonLadderStudios/MoonMind/issues/3703) ([Omnigent control plane 2/11]).

**Implementation tracking:** rollout notes and temporary handoffs belong under `docs/tmp/` or gitignored local-only artifacts, not in this canonical design document.

## Related docs

- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md)

## Why

The legacy `omnigent_bridge_sessions` row (see OmnigentBridge §7.1) simultaneously
carries Workflow/run/Step/AgentRun identity, request idempotency, provider
session and host identity, Provider Profile and host-lease authority, chat-binding
identity, session lifecycle status, first-message submission state, and broad
mutable metadata. This conflates one logical provider session with every
request, turn, continuation, and cleanup attempt against it. #3685 documents a
production shape where seven bridge rows and chat bindings pointed at one
provider session, and a newer continuation row could become terminal and
supersede the real canonical chat authority while the provider session was still
active.

## Durable aggregates

The control plane separates the durable concepts so illegal lifecycle ownership
is hard or impossible to represent. Table names are declarative; the ownership
rules are the contract.

| Aggregate | Table | Owns | Never owns |
|-----------|-------|------|------------|
| `OmnigentSession` | `omnigent_sessions` | canonical provider-session authority, the single opaque chat binding, immutable intent, desired/observed/reconciled state, session terminality, revision + fencing generation, next reconciliation deadline | request idempotency |
| `OmnigentTurnAttempt` | `omnigent_turn_attempts` | request idempotency, instruction digest, provider turn/item identity, attempt delivery lifecycle and attempt terminality | `chat_binding_id`; it cannot terminalize the session |
| `OmnigentObservation` | `omnigent_observations` | append-only bounded index over authoritative observations; full redacted payloads live in artifacts (`payload_ref`) | authority — it is evidence, not state |
| `OmnigentCommand` | `omnigent_commands` | durable logical-side-effect / idempotency journal (identity, expected session revision, fencing generation, payload digest, delivery ambiguity, provider receipt) | orchestration authority — Temporal remains authoritative |
| `OmnigentReconciliationDecision` | `omnigent_reconciliation_decisions` | append-only record of input/frontier digests, expected revision + fencing, decision/reason codes, resulting command or next deadline | mutable state |
| `OmnigentChatBindingAlias` | `omnigent_chat_binding_aliases` | resolution of previously issued chat-binding handles to the canonical session, or a fail-closed diagnostic | provider session IDs (never exposed) |

Every aggregate carries a `schema_version`; reading or writing an unsupported
version fails closed (Compatibility Policy) rather than silently coercing.

## Invariants

- **One canonical authority per scope.** A unique index over
  `(moonmind_workflow_id, provider_session_ref)` prevents a second canonical
  session for a Workflow/provider-session scope. `NULL` provider sessions stay
  distinct, so pre-attachment rows remain independent.
- **One chat authority.** A unique index over `chat_binding_id` guarantees one
  logical binding maps to one canonical session. A continuation or remediation
  turn reuses the canonical session and has no affordance to allocate another
  binding — the turn-attempt model has no `chat_binding_id` column at all.
- **Attempt terminality ≠ session terminality.** They live on different rows; a
  terminal attempt does not terminalize the session, and a terminal session
  cannot be moved back to a nonterminal state by an ordinary update.
- **Fail closed on conflicting immutable authority.** Conflicts are rejected at
  the repository boundary rather than selecting the newest row.

## Repository boundaries

Application and domain code depend on the repositories and frozen records in
`moonmind.omnigent.control_plane`, never on the SQLAlchemy models directly:
`SessionRepository`, `TurnAttemptRepository`, `ObservationRepository`,
`CommandRepository`, `DecisionRepository`, `ChatBindingAliasRepository`. The
create-session / allocate-chat-binding / bind-immutable-authority /
establish-first-turn sequence runs atomically in one transaction
(`OmnigentControlPlaneStore.establish_session`).

## Migration and compatibility

The schema migration (`356_omnigent_ctrl_plane`) is additive: it creates the new
tables and leaves the legacy bridge rows, Temporal histories, and legacy read
code operational. Retirement of the legacy tables is owned by a later
control-plane rollout issue.

`moonmind.omnigent.control_plane.backfill` derives canonical sessions
deterministically from existing bridge rows:

1. Group bridge rows by `(workflow, provider session)` authority.
2. Select a canonical session only when the group's immutable authority is
   complete and nonconflicting; **never** choose by `updated_at`.
3. Convert each request-specific row into turn-attempt lineage (earliest row is
   `initial`; the rest are `continuation`).
4. Preserve every event/artifact/snapshot/diagnostic/terminal ref as an
   append-only migration observation.
5. Keep previously issued chat-binding URLs as safe aliases to the canonical
   authority, or as stable fail-closed diagnostics for quarantined groups.
6. Quarantine groups with conflicting immutable authority fail-closed.

Both `dry_run` and idempotent apply modes are supported: repeat dry-run and
repeat apply produce the same plan and leave the same rows.

## Non-goals

- The dedicated session Temporal workflow.
- The native Omnigent SPA work owned by #3685.
- Deleting legacy tables or code before the migration/rollout issue authorizes
  retirement.
