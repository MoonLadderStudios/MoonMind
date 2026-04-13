# Agent Session Deployment Safety Cutover

Status: active implementation note
Spec: `specs/165-agent-session-deployment-safety/`
Scope: Codex managed sessions only; delayed standalone-image delivery is out of scope.

This file records cutover gates for deployment-sensitive managed-session workflow behavior. It is temporary migration/backlog material and should be removed when the rollout is complete and the desired-state docs are fully aligned.

## Shared Prerequisites

- Replay-safe rollout gates or an explicit cutover plan are approved for incompatible workflow-shape changes.
- Representative `AgentSessionWorkflow` histories replay successfully.
- Fault-injected lifecycle tests pass for termination cleanup, cancel semantics, race/idempotency, Continue-As-New carry-forward, and reconcile outcomes.
- Bounded workflow metadata, Search Attributes, activity summaries, telemetry dimensions, schedule metadata, and replay fixtures are checked for sensitive or unbounded content.
- `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime` passes before broad rollout.

## Enabling `SteerTurn`

Prerequisites:

- Workflow `SteerTurn` update rejects stale epochs and missing active turns before mutation.
- Runtime and controller steering paths use the Codex App Server transport and return bounded results.
- Unsupported runtime steering is classified as a permanent failure, not a transient retry.

Validation gates:

- `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`
- `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

Rollback/removal:

- Disable callers from issuing `SteerTurn` while leaving replay-safe workflow history handling in place.
- Remove any temporary patch only after representative histories containing the patch age out or replay with the replacement path.

## Enabling Continue-As-New

Prerequisites:

- Continue-As-New is initiated from the workflow main run path.
- Accepted async handlers drain before handoff.
- Carry-forward payload contains only binding identity, epoch, locator, compact control metadata, continuity refs, degradation state, and bounded request-tracking state.

Validation gates:

- `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- `tests/unit/workflows/temporal/test_agent_session_replayer.py`
- `tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py`

Rollback/removal:

- Raise the event threshold or disable the feature flag/configuration that forces early handoff.
- Keep replay compatibility for histories that already crossed a Continue-As-New boundary.

## Changing Cancel/Terminate Semantics

Prerequisites:

- `CancelSession` stops active work without treating runtime container cleanup as complete.
- `TerminateSession` waits for runtime cleanup and supervision finalization before completing the workflow.
- Parent workflow shutdown races prefer idempotent terminate behavior and do not leave orphaned session records.

Validation gates:

- `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
- `tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- `tests/unit/workflows/temporal/test_agent_runtime_activities.py`

Rollback/removal:

- Stop issuing new semantic-changing controls from operators or parent workflows.
- Preserve replay-safe handling for histories that already contain the changed update/signal events until replay validation proves removal is safe.

## Introducing Visibility Metadata

Prerequisites:

- New Search Attributes are registered before workers emit them.
- Workflow current details and activity summaries use only bounded identifiers, statuses, booleans, and artifact refs.
- Prompts, transcripts, terminal scrollback, raw logs, credentials, secrets, and unbounded provider output are excluded.

Validation gates:

- `tests/unit/workflows/temporal/workflows/test_agent_session.py`
- `tests/unit/workflows/temporal/test_client_schedules.py`
- `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- Forbidden-content scan in `specs/165-agent-session-deployment-safety/quickstart.md`

Rollback/removal:

- Stop upserting the new metadata field before removing any associated worker or UI dependency.
- Keep old replay-visible state readable until representative histories replay under the replacement workflow definition.
