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
4. refuses a remediation turn that broadens harness, execution realizer,
   Provider Profile, model, workspace, Skill, launch policy, or publication
   authority;
5. records a distinct `OmnigentTurnAttempt` and one command-journal entry
   carrying source kind, instruction digest, idempotency identity, expected
   revision, fencing generation, delivery state, and terminal evidence refs;
6. fences incompatible cleanup for the accepted turn.

An instruction may *request* authority but never attests it: recorded authority
comes only from durable session state. A dimension the instruction does not
assert is not a request to change it and leaves the session's authority intact.

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

## Harness neutrality

The canonical turn service contains no Codex-versus-OpenCode lifecycle branches.
`moonmind/omnigent/realizers/turn_delivery.py` owns the shared claim → run →
settle wrapper, and both `codex-profile-bound@1` and `generic-omnigent-host@1`
use it; only the wrapped lifecycle differs. Immutable authority is projected
from the recorded execution plan by
`moonmind/omnigent/turn_authority.py`, which reads plan payload fields rather
than harness-specific state.

## Non-goals

- Reimplementing the hosted Omnigent SPA or route inventory owned by #3635.
- Granting new terminal, browser, file-write, model-control, or subagent
  capabilities.
- Treating direct Codex as an implicit continuation fallback.
