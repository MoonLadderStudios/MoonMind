# Omnigent Control-Plane Aggregates

Status: Proposed design
Document Class: System / Feature Design View
Owners: MoonMind Platform
Last updated: 2026-08-21

**Issues:** [MoonLadderStudios/MoonMind#3703](https://github.com/MoonLadderStudios/MoonMind/issues/3703) ([Omnigent control plane 2/11]), [MoonLadderStudios/MoonMind#3707](https://github.com/MoonLadderStudios/MoonMind/issues/3707) (canonical turn-command boundary).

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
| `OmnigentTurnAttempt` | `omnigent_turn_attempts` | canonical turn source, request idempotency, instruction digest, the immutable execution authority the turn was admitted against, provider turn/item identity, attempt delivery lifecycle and attempt terminality | `chat_binding_id`; it cannot terminalize the session, and it cannot replace the execution plan or runtime binding |
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

## Canonical turn sources and the one turn-command boundary

**Issue:** [MoonLadderStudios/MoonMind#3707](https://github.com/MoonLadderStudios/MoonMind/issues/3707).

`OmnigentTurnAttempt.turn_source` is a **closed, versioned vocabulary**
(`moonmind.omnigent-turn-source/v1`) with exactly eight members:

```text
initial
repository_continuation
remediation
workflow_chat
steering
approval_response
checkpoint_resume
linked_branch
```

Source kind changes **authorization, evidence, and reuse policy only**. It never
changes the command, idempotency, fencing, observation, or terminality model.
`moonmind.omnigent.turn_contracts.TURN_SOURCE_POLICIES` holds the one policy per
source, and `TURN_PRODUCER_SOURCES` is the inventory of every production
follow-up producer bound to exactly one source. A producer absent from that
inventory cannot reach the boundary at all.

`moonmind.omnigent.control_plane.turn_service.CanonicalTurnService` is the only
application service permitted to submit new work to an existing session. For
every source kind it owns, once:

- turn-attempt identity and idempotency (logical identity is
  `(session_id, turn_source, instruction_digest)` under the caller's key),
- the immutable execution authority the turn was admitted against
  (`execution_plan_ref`, `runtime_binding_ref`, `authority_digest`),
- the fenced `omnigent.submit_turn` command journal entry,
- an append-only admission decision — including for refused submissions,
- cleanup fencing before any provider mutation.

The service is harness-neutral by contract: the harness appears only as one
immutable authority dimension compared against the recorded execution plan.
Harness-specific message or resume behavior lives behind the selected Omnigent
adapter or realizer; the session supervisor retains canonical ownership.

### One evidence-gated resume decision

`moonmind.omnigent.turn_contracts.TurnDisposition` is the single decision
vocabulary shared by turn admission and checkpoint recovery
(`moonmind.omnigent.checkpoints.recovery_mode`):

| Disposition | Meaning |
|-------------|---------|
| `live_reattach` | every original runtime authority is still current |
| `cold_restore` | artifact-backed checkpoint and workspace evidence exists |
| `branch_required` | an immutable execution dimension changed |
| `new_session_required` | the prior session cannot be reused (terminal, cleaned up, read-only, or a branch source) |
| `resume_unavailable` | no safe resume path exists from the presented evidence |

Live reattach requires *complete* current authority; cold restore is never
selected merely because live reattach failed, so a destroyed host-local path can
never be mistaken for restore evidence. Remediation additionally may not change
any dimension in `REMEDIATION_LOCKED_DIMENSIONS` — it narrows work and never
broadens harness, profile, model, repository, branch, workspace, Skill, launch,
publication, or policy authority.

### Authority versus transport

Native Workflow Chat mutations are admitted through this boundary before the
bridge control journal records a transport claim, so the bridge is a receipt and
never a second submission authority. A binding with no canonical session row is a
pre-canonical legacy session and stays on the legacy path rather than being
migrated outside the #3712 handoff contract.

`moonmind.omnigent.supervisor_turn_dispatch.SupervisorTurnDispatcher` is the one
production sender of the `submit_authorized_continuation` signal. It runs only
after the turn attempt and command are durably committed, and it fails closed
when handed a refused decision.

## Repository boundaries

Application and domain code depend on the repositories and frozen records in
`moonmind.omnigent.control_plane`, never on the SQLAlchemy models directly:
`SessionRepository`, `TurnAttemptRepository`, `ObservationRepository`,
`CommandRepository`, `DecisionRepository`, `ChatBindingAliasRepository`,
`CleanupAuthorityRepository`. The
create-session / allocate-chat-binding / bind-immutable-authority /
establish-first-turn sequence runs atomically in one transaction
(`OmnigentControlPlaneStore.establish_session`).

## Migration and compatibility

`358_omnigent_turn_source` replaces the free-form `lineage_kind` column with
`turn_source` and adds the turn-bound immutable execution authority columns.
Historical values map deterministically: `initial` and the pre-#3707 default
`instruction` become `initial`, and every other value becomes
`repository_continuation` (the source kind that describes a same-session
follow-up). No historical value maps to a source with broader authority than the
row already had.

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
   `initial`; the rest are `repository_continuation`).
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
