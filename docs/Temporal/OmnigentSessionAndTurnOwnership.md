# Omnigent Session and Turn Ownership

Canonical, declarative desired-state for the Omnigent control plane's session and
turn ownership contract. Tracking issue: MoonLadderStudios/MoonMind#3707
(parent epic #3701).

Same-session continuations, remediation attempts, checkpoint recovery, user chat
messages, steering actions, approval responses, and linked branches all route
through **one canonical `OmnigentSession` and `OmnigentTurnAttempt` ownership
model**. One provider-session authority and one chat binding are preserved
whenever policy authorizes session reuse. New work is represented by a **new turn
attempt** or an **explicit branch** — never by minting another ambiguous
bridge-session row.

The runtime-neutral contract lives in `moonmind/omnigent/session_turns.py`; the
recovery-decision boundary lives in `moonmind/omnigent/checkpoints.py`.

## Lifecycle ownership matrix

Each lifecycle has exactly one owner and one terminal meaning. **No lifecycle may
infer another lifecycle's terminality merely from matching timestamps or a shared
provider ID.** The matrix is enforced in code as
`session_turns.LIFECYCLE_OWNERSHIP`.

| Lifecycle | Owner | Terminal meaning |
|---|---|---|
| Workflow Execution | `MoonMind.UserWorkflow` | Product workflow completed |
| Step Execution | Step ledger | This logical Step execution completed |
| AgentRun | `MoonMind.AgentRun` | One agent execution contract completed |
| Omnigent session | `MoonMind.OmnigentSession` | Provider session is no longer active or resumable under policy |
| Turn attempt | Session workflow | One submitted instruction reached a terminal outcome |
| Remediation loop | Remediation controller | Candidate passed, policy exhausted, or intervention required |
| Checkpoint branch | Branch workflow/ledger | Branch head reached a durable outcome |
| Chat binding | Canonical session authority | Caller can read or mutate the canonical session |
| Host lease | Host manager | Host realization is no longer reserved |
| Provider Profile lease | Profile manager | Credential consumer is gone and capacity is releasable |

Attempt terminality, session terminality, Workflow terminality, and
remediation-loop terminality remain distinct. A turn attempt reaching a terminal
outcome never terminalizes the canonical session:
`session_turns.session_is_terminal(...)` returns terminal only when session policy
**and** authoritative session evidence require it, and deliberately ignores
attempt completion so it cannot be smuggled in as a session-terminality signal.

## Turn submission contract

All provider messages use one typed command path (`session_turns.TurnSubmission`):

```
create turn attempt
  -> validate immutable session and caller/controller authority
  -> outbound security scan where required
  -> prepare digest and idempotency marker
  -> claim fenced submit command
  -> submit or reconcile delivery_unknown
  -> observe provider turn
  -> record terminal attempt evidence
```

The path supports these source kinds (`session_turns.TurnSourceKind`); source kind
affects authorization and policy, not the idempotency or observation model:

`initial`, `repository_continuation`, `remediation`, `workflow_chat`, `steering`,
`approval_response`, `checkpoint_resume`, `linked_branch`.

A turn attempt's identity (`turn_attempt_id`, `idempotency_key`) is always
distinct from the canonical session it addresses. Delivery is observed through
`TurnDeliveryState` (`pending` → `delivered` / `delivery_unknown`); a
`delivery_unknown` outcome is reconciled, never duplicated.

## Same-session continuation

A continuation reuses the provider session only when the compiled intent and
current reconciled state authorize it. `session_turns.decide_continuation(current,
requested)` compares the immutable dimensions and returns either
`accept_same_session` or `branch_required`; `build_continuation_turn(...)` then:

- reuses the canonical session id, chat binding, immutable profile/policy/image/
  compatibility/workspace authority;
- allocates a new turn-attempt id and idempotency key;
- never allocates another chat binding or canonical session row;
- refuses to continue a terminal session (an explicit linked-branch or new-session
  policy is required instead);
- rejects `initial` and `linked_branch` source kinds, which establish new session
  authority rather than continuing one.

Changed immutable dimensions produce `branch_required` rather than silently
mutating the existing session.

## Recovery decision boundary

Checkpoint recovery, continuation, and branch decisions share **one typed
vocabulary**, `checkpoints.OmnigentRecoveryMode`:

`live_reattach`, `cold_restore`, `branch_required`, `resume_unavailable`.

`checkpoints.decide_recovery(...)` is the single classifier. Immutable input
changes always win over live/cold availability (a changed dimension →
`branch_required`); availability is authority-sensitive and supplied only by the
trusted Activity after re-resolving current profile, lease, host, session, cursor,
and first-message state.

- **live_reattach** requires the current Provider Profile lease, host, provider
  session, cursor, first-message, and credential generation to all still be valid.
- **cold_restore** uses artifact-backed workspace and session evidence and never
  trusts a stale host-local path; the source workspace, process, host, and
  session may be destroyed before cold restore and are not required afterward.
- **branch_required** is emitted when any immutable dimension
  (`instructionDigest`, `runtimeId`, `model`, `effort`, `providerProfileId`,
  `launchPolicyRef`, `repositoryBranch`, `publishMode` —
  `checkpoints.IMMUTABLE_RECOVERY_DIMENSIONS`) changed.
- **resume_unavailable** is emitted when authority evidence is missing or no
  authorized path remains.

The Temporal activity boundary
(`workflows/temporal/activities/omnigent_activities._checkpoint_recovery_decision`)
delegates to `decide_recovery` and renders the same compact
`{"recoveryAction": ..., "reasonCodes": [...]}` payload, so in-flight histories and
persisted `recoveryAction` values keep deserializing.

## Remediation integration

The remediation controller submits a typed `session_turns.RemediationTurnIntent`
that carries loop and attempt identity, exact durable gate-result and
remaining-work refs, candidate workspace and checkpoint refs, remediator Skill and
runtime authority, verification requirements, attempt and branch budgets, whether
same-session reuse is allowed, and required production-boundary evidence. The
intent:

- validates that every ref is a durable artifact/credential reference, never raw
  credential data or a local path;
- structurally cannot broaden profile, workspace, or publication authority —
  `assert_within_session_authority(session)` rejects any requested change to the
  canonical Provider Profile, publication mode, or workspace;
- compiles into a `remediation` source-kind turn only when same-session reuse is
  allowed; otherwise the controller must branch.

The session workflow owns provider turn submission; the remediation controller
owns whether another attempt is admitted. Neither fabricates the other's result.
Cumulative remediation preserves prior side effects and candidate state without
replaying the original turn and remains compatible with #3480 acceptance.

## Workflow Chat integration

- The chat binding belongs only to the canonical session; browser-visible
  capability and read-only state derive from canonical immutable authority plus
  the current reconciled session and caller state
  (`effective_capabilities.resolve_bridge_row_capabilities`).
- An attempt row cannot supersede chat authority by being newer.
- Final authoritative session terminality flips the same binding to read-only.
- Historical transcript and diagnostic reads remain available after host and
  provider cleanup.
- #3685 and #3642 remain authoritative for the compiled UI and browser/network
  acceptance; this contract is the server-side authority they consume.

## Cleanup coordination

Cleanup, continuation admission, publication, session stop, host cleanup,
Provider Profile release, and janitor recovery are deterministically coordinated:
an accepted new turn cancels or fences incompatible cleanup before provider
mutation, and a continuation submitted after terminal cleanup requires an explicit
linked-branch or new-session policy rather than resurrecting deleted provider
authority (`build_continuation_turn` refuses to continue a terminal session).

## Scope

This document and `session_turns.py` define the shared contract every consumer
honours and unify the recovery-decision vocabulary. Durable persistence of the
canonical session/turn rows (migrating the existing `OmnigentBridgeSession`
storage) is owned by the schema and store work tracked under the parent
control-plane epic (#3701, depends on #3703/#3705/#3706) and is intentionally out
of scope for this contract layer.
