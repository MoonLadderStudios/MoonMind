# Contract: Agent Session Workflow Hardening

## Scope

This contract defines the workflow-visible behavior for Phase 3 hardening of `MoonMind.AgentSession`. It covers update handling, readiness, query state, and Continue-As-New handoff. It does not define new HTTP endpoints or frontend behavior.

## Workflow Query: `get_status`

Returns the current bounded managed-session snapshot.

Required fields:

- `binding.workflowId`
- `binding.taskRunId`
- `binding.sessionId`
- `binding.sessionEpoch`
- `binding.runtimeId`
- `binding.executionProfileRef`
- `status`
- `containerId`
- `threadId`
- `activeTurnId`
- `lastControlAction`
- `lastControlReason`
- `latestSummaryRef`
- `latestCheckpointRef`
- `latestControlEventRef`
- `latestResetBoundaryRef`
- `terminationRequested`

Contract rules:

- Query state must reflect complete ordered mutator outcomes.
- Query state must not expose prompts, transcripts, terminal scrollback, secrets, or large artifact bodies.
- Continuity refs must represent latest known durable refs after successful projection refresh.

## Runtime-Bound Updates

Affected updates:

- `SendFollowUp`
- `InterruptTurn`
- `SteerTurn`
- `ClearSession`

Contract rules:

- Accepted updates that need a runtime locator must wait until container and thread handles are attached.
- Runtime-bound activities must not be invoked before the locator is complete.
- Turn-scoped updates must require an active turn after readiness.
- Stale epoch, duplicate clear, and terminating/terminated state remain deterministic validation failures.

## Workflow-Level Serialization

Affected shared state:

- Runtime locator: `containerId`, `threadId`
- `activeTurnId`
- `status`
- `lastControlAction`
- `lastControlReason`
- Continuity refs
- `terminationRequested`
- Compact request-tracking state

Contract rules:

- Async update handlers that mutate this state must run under a workflow-level lock.
- A mutator must observe the complete state left by the previous mutator.
- Direct fire-and-forget state propagation signals remain outside the async mutator lock unless they become async mutators.

## Completion Drain

Contract rules:

- Before returning a terminal result, the workflow must wait for accepted async handlers to finish.
- The terminal query result must be retrievable without interrupting an accepted update result.

## Continue-As-New Handoff

Trigger sources:

- Temporal server suggestion.
- Explicit shortened-history threshold used for validation.

Contract rules:

- Handoff must be initiated from the main workflow run path.
- Handoff must wait for accepted async handlers to finish before continuing as new.
- Handoff payload must include:
  - task run ID
  - runtime ID
  - execution profile ref when present
  - session ID
  - session epoch
  - container ID when attached
  - thread ID when attached
  - active turn ID when present
  - last control action
  - last control reason
  - latest summary ref
  - latest checkpoint ref
  - latest control event ref
  - latest reset boundary ref
  - shortened-history threshold when present
  - compact request-tracking state when present
- Handoff payload must exclude prompts, transcripts, terminal scrollback, secrets, and large artifact content.

## Validation Contract

Required test coverage:

- Validator regression for stale epoch, missing active turn, duplicate clear, and post-termination mutation.
- Lock serialization for async mutators.
- Readiness wait for runtime-bound update before handle attachment.
- Handler drain before workflow completion.
- Handler drain before Continue-As-New.
- Handoff payload carry-forward for identity, epoch, locator, active turn, control metadata, continuity refs, and compact request-tracking state.
