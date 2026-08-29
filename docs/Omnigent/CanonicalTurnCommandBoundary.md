# Omnigent Canonical Turn-Command Boundary

Status: Implemented
Document Class: System / Feature Design View
Owners: MoonMind Platform
Last updated: 2026-08-29

**Issue:** [MoonLadderStudios/MoonMind#3707](https://github.com/MoonLadderStudios/MoonMind/issues/3707) ([Omnigent control plane 7/11]).

## Related docs

- [`docs/Omnigent/ControlPlaneAggregates.md`](./ControlPlaneAggregates.md)
- [`docs/Omnigent/ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md)
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md)

## Rule

One typed canonical turn-command path is the only way to submit new work to an
existing Omnigent session. A continuation, remediation attempt, checkpoint turn,
Workflow Chat message, steering action, approval response, or linked branch does
not become an independent bridge authority merely because it enters through a
different API, workflow, or adapter.

When policy authorizes same-session reuse, every instruction source preserves:

- one canonical session
- one chat binding
- one immutable execution plan
- one current runtime binding and fencing generation
- one provider-session attachment
- a distinct turn-attempt identity and evidence per instruction

When immutable authority changes, or the prior session is no longer safely
reusable, the boundary returns an explicit typed decision. It never silently
mutates the old session and never selects another harness or realizer.

## Canonical turn sources

`moonmind/omnigent/control_plane/turn_sources.py` owns the closed, versioned
vocabulary (`TURN_SOURCE_VOCABULARY_VERSION = 1`):

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

The source is durable on `omnigent_turn_attempts.lineage_kind`, enforced by a
database `CHECK` constraint and by `coerce_turn_source`, which fails closed. The
source changes authorization, evidence, and policy; it never changes the command,
idempotency, fencing, observation, or terminality model.

### Where a producer's source comes from

No producer names its own source as a literal. The source is derived from typed
request authority at the boundary the producer already uses:

- `moonmind/omnigent/realizers/turn_delivery.py` derives it with
  `canonical_turn_source(request)` for both execution realizers. The source comes
  from `stepExecution.canonicalTurnLineage`, the launching controller's typed
  attestation. `workflows/run.py` builds that block in
  `_record_canonical_turn_lineage` from workflow-owned remediation-loop state
  only — an admitted loop, its current attempt ordinal, and its recorded head —
  and refuses any plan- or browser-authored `canonicalTurnLineage`, so its
  presence is the capability, not a hint. A node whose annotations merely *look*
  like a remediation attempt carries no lineage unless the admitted loop
  controller attests that attempt. Emitting the block into the AgentRun request
  is gated by the `run-canonical-turn-lineage-v1` replay patch, so an AgentRun
  started before the cutover carries no lineage and remains an `initial` turn.
- `api_service/api/routers/omnigent_bridge.py` maps native Workflow Chat control
  types to `workflow_chat`, `approval_response`, or `steering` by exhaustive
  membership.
- `moonmind/omnigent/profile_bound_execution.py` claims
  `repository_continuation` for each repository-output continuation, and
  `api_service/services/checkpoint_branch_turn_execution.py` claims
  `checkpoint_resume`.

The instruction that bootstraps a canonical session journals its own source on
the bootstrap attempt, so a remediation attempt that opens its own session is
not recorded as `initial`. A later follow-up that canonicalizes an older session
on demand cannot attest the earlier instruction's source, so that bootstrap
attempt keeps `initial`.

`linked_branch` names a linked-branch turn that targets an *existing* canonical
session. The **Continue in a new workflow** action deliberately reserves a new
destination Workflow Execution instead (`api_service/services/linked_continuation.py`),
so that journey records its own `initial` turn on its own canonical session —
which is the same rule as `branch_required`: a branch gets a new canonical
session and the prior session is never mutated.

## Turn admission

`CanonicalTurnCommandService.claim` is the boundary. Before any provider
mutation it:

1. resolves exactly one canonical session (chat-binding alias, provider-session
   scope, or a verified deterministic bootstrap);
2. verifies the caller's principal against the session's recorded owner;
3. loads the recorded execution plan and runtime binding from durable session
   authority and compares them against the authority the caller requests;
4. refuses a remediation turn that broadens *any* immutable dimension —
   harness, execution realizer, Provider Profile and its credential generation,
   model, effort, repository, branch, workspace, Skill, launch policy, or
   publication authority — comparing against the authority of the attempt it
   repairs (see **What bounds a remediation turn** below);
5. records a distinct `OmnigentTurnAttempt` and one command-journal entry
   carrying source kind, instruction digest, idempotency identity, expected
   revision, fencing generation, delivery state, and terminal evidence refs;
6. fences incompatible cleanup for the accepted turn.

An instruction may *request* authority but never attests it: recorded authority
comes only from durable session state. A dimension the instruction does not
assert is not a request to change it and leaves the session's authority intact.

### What bounds a remediation turn

Each remediation attempt of a workflow-owned loop is materialized as its own
logical step, so it launches its own Step Execution and therefore bootstraps its
own canonical session. Two designs could make AC6 meaningful; MoonMind takes the
second:

- *Rejected:* resolve the remediation turn onto the repaired attempt's canonical
  session. Each attempt is a distinct AgentRun with its own provider-session
  attachment, and one canonical session owns exactly one such attachment, so
  reusing the prior attempt's session would collapse two provider sessions onto
  one aggregate and break the Step Execution ↔ session identity the bootstrap
  already enforces.
- *Chosen:* compare the requested authority against the loop's recorded base
  authority. `canonicalTurnLineage.baseStepExecutionId` names the Step Execution
  whose candidate the attempt continues — the loop head's
  `headStepExecutionId`, which is the original implementation step for the first
  attempt and the previous attempt afterwards. `CanonicalTurnCommandService`
  loads that Step Execution's canonical session
  (`SessionRepository.get_by_step_execution`) and uses *its* durable
  `immutableTurnAuthority` as the bound.

The instruction names the base identity; it never attests the base authority. A
claim that bootstrapped the session it is claiming against is never its own
bound — otherwise the guard would compare the request with the metadata it just
wrote. A turn that joins an *existing* session is still bounded by that session's
own durable record. When the repaired Step Execution never established a
canonical session, there is no prior Omnigent authority to broaden and the turn
is admitted; that is an absence of recorded authority, not a comparison against
the claim itself.

## One typed resume decision

`moonmind/omnigent/resume_decision.py` owns the single closed decision
vocabulary shared by checkpoint recovery, turn admission, and the session
supervisor:

```text
live_reattach
cold_restore
branch_required
new_session_required
resume_unavailable
```

- `live_reattach` requires current runtime authority.
- `cold_restore` uses artifact-backed checkpoint and workspace evidence and never
  depends on a destroyed host-local path.
- `branch_required` is returned when immutable authority changed and the
  checkpoint carries branch-capable evidence; a branch gets its own canonical
  session.
- `new_session_required` is returned when the prior session cannot be branched
  from — no branch-capable evidence, or durable session terminality.
- `resume_unavailable` is the fail-closed answer when authority evidence is
  missing or ambiguous.

## Distinct terminal meanings

`TerminalMeaning` enumerates the planes that terminalize independently:

```text
turn_attempt   provider_session   agent_run   step_execution
workflow       remediation_controller         branch      cleanup
```

Turn, provider-session, and cleanup terminality live on their own durable
aggregates. The remaining planes are recorded through `terminal_meaning_patch`
into namespaced session metadata, so one plane can never overwrite another and
none of them can write `SessionRecord.terminal_state`. `build_timeline` projects
all eight side by side under `terminal.meanings`.

A completed turn does not terminalize the session. A terminal Workflow does not
erase the session's historical-read authority: transcript, timeline, and
evidence remain readable after provider, host, credential, and workspace cleanup.

## Continuation versus cleanup

An accepted turn advances the cleanup generation before any provider mutation.
The race is therefore resolved in one direction:

- a janitor holding an older cleanup claim is fenced out at completion and cannot
  delete the replacement generation;
- a janitor that has not yet claimed simply claims the newer generation after the
  turn;
- completed cleanup is never reopened — the turn is refused so it cannot consume
  credentials whose lease was already released, and the caller cold-restores or
  branches instead.

That fence is only real because every production teardown owner claims the *same*
aggregate. `moonmind/omnigent/control_plane/cleanup_authority.py` owns that one
claim, and both owners hold it before releasing anything and settle it after the
last release:

- the legacy session supervisor (`omnigent.stop_provider_session`,
  `omnigent.stop_host`, `omnigent.release_leases`), and
- the generic Omnigent host realizer, for both in-band cleanup and janitor
  recovery.

An owner that cannot win the claim performs no destructive step, and an owner
whose generation was advanced by an admitted turn cannot report cleanup complete.
Host-lease and OAuth-host compare-and-swaps remain each owner's own concurrency
control; they are not a substitute for the shared claim.

## Signal-driven continuations

`submit_authorized_continuation` is an ordinary instruction source. The
`omnigent.persist_signal_intents` Activity claims it through
`CanonicalTurnCommandService.claim_with_repositories`, so it receives the same
command-journal entry, immutable-authority comparison, principal verification,
and cleanup fence as every other source. The signal names its `requestId` and
durable `instructionRef`; the boundary derives the turn-attempt identity, because
an instruction may request authority but never attests it.

## Harness neutrality

The canonical turn service contains no Codex-versus-OpenCode lifecycle branches.
`moonmind/omnigent/realizers/turn_delivery.py` owns the shared claim → run →
settle wrapper, and both `codex-profile-bound@1` and `generic-omnigent-host@1`
use it; only the wrapped lifecycle differs. Immutable authority is projected
from the recorded execution plan by
`moonmind/omnigent/turn_authority.py`, which reads plan payload fields rather
than harness-specific state.

Codex reaches the coordinator through two production entrypoints — the realizer
registry and `integration.omnigent.execute` for profile-bound requests carrying
no execution plan and no Agent Profile ref. Both construct
`OmnigentProfileBoundExecutionCoordinator` with the canonical turn service, so
neither has a repository continuation that submits outside the boundary.

`integration.omnigent.execute` also serves the unprofiled path, for requests
with no `executionProfileRef`. That path compiles no execution plan, so it
asserts no plan-derived immutable authority, but it still creates a provider
session and posts its first message. It therefore runs inside the same
`deliver_canonical_turn` wrapper, which claims the command, requires an owned
claim, fences cleanup, attaches the delivered provider session, and settles.
No production Omnigent execution path mutates the provider outside this
boundary.

## Non-goals

- Reimplementing the hosted Omnigent SPA or route inventory owned by #3635.
- Granting new terminal, browser, file-write, model-control, or subagent
  capabilities.
- Treating direct Codex as an implicit continuation fallback.
