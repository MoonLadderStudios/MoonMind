# Contract: Codex Managed Session Phase 4/5 Hardening

## Scope

This contract defines the runtime behavior and validation surfaces for the remaining Codex managed session Phase 4 and Phase 5 work. It is not a public HTTP API contract.

## Operator Visibility Contract

The managed session workflow must expose bounded operator metadata for:

- task run identity
- runtime identity
- session identity
- session epoch
- session status
- degradation flag
- latest summary/checkpoint/control/reset artifact references when available

Forbidden in all visibility and summary surfaces:

- prompts
- transcripts
- terminal scrollback
- raw logs
- raw provider output
- raw error bodies
- credentials
- secret values

Indexed fields are limited to:

- `TaskRunId`
- `RuntimeId`
- `SessionId`
- `SessionEpoch`
- `SessionStatus`
- `IsDegraded`

## Control History Contract

Launch, send, interrupt, clear, cancel, steer, and terminate operations must produce readable bounded summaries that identify:

- operation family
- session identity
- epoch when relevant
- container/thread/turn identifiers when already bounded and attached

Summaries must not expose instructions, raw output, raw errors, transcripts, logs, or secrets.

## Runtime Separation Contract

Runtime/container side effects must execute through the runtime activity boundary. Workflow-processing workers may schedule and observe those operations but must not perform Docker/container mutation directly.

The contract applies to:

- launch
- send
- steer
- interrupt
- clear
- cancel
- terminate
- recurring reconcile/sweeper work

## Reconcile Contract

Recurring reconciliation must:

- inspect managed session records and runtime state
- reattach supervision when safe
- mark sessions degraded when runtime state is missing or inconsistent
- detect orphaned runtime state
- return a bounded outcome

The reconcile outcome must include counts and compact identifiers only. It must not include raw records, raw logs, transcripts, prompts, credentials, or container dumps.

## Lifecycle Validation Contract

Required validation must cover:

- task-scoped session creation
- runtime handle attachment
- follow-up turn execution
- query state and continuity refs
- `clear_session` invariants
- `interrupt_turn` end-to-end behavior
- `terminate_session` cleanup and no-orphan outcome
- `cancel_session` behavior distinct from termination
- `steer_turn` deterministic unavailable behavior and enabled success path
- restart/reconcile behavior
- duplicate and stale-epoch controls
- update-before-handles-attached behavior
- parent/session shutdown races
- Continue-As-New carry-forward
- replay validation for workflow-shape changes

## Replay Safety Contract

Any managed session workflow-shape change affecting handlers, payload state, control names, signal/update semantics, or Continue-As-New carry-forward must include replay validation or a documented explicit cutover path.

Replay fixtures must be credential-free and must not contain prompts, transcripts, raw logs, scrollback, raw provider output, or secrets.
