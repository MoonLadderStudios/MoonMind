# Data Model: Temporal Editing Hardening

## Temporal Task Editing Event

**Purpose**: Bounded observable record for a task editing action or outcome.

**Fields**:

- `event`: Event name. Allowed values:
  - `detail_edit_click`
  - `detail_rerun_click`
  - `draft_reconstruction_success`
  - `draft_reconstruction_failure`
  - `update_submit_attempt`
  - `update_submit_result`
  - `rerun_submit_attempt`
  - `rerun_submit_result`
- `mode`: One of `detail`, `edit`, or `rerun`.
- `workflowId`: Temporal execution identity, present when an execution context exists.
- `updateName`: `UpdateInputs` or `RequestRerun` for submit events.
- `result`: `success` or `failure` for result events.
- `applied`: Backend application outcome when available, such as immediate, safe point, or continue-as-new semantics.
- `reason`: Normalized bounded failure reason when available.

**Validation Rules**:

- Event names must come from the allowed set.
- Raw task instructions, artifact contents, credentials, and full payloads are forbidden.
- String dimensions must be bounded before emission.
- Telemetry failures must be swallowed and must not affect runtime behavior.

**Relationships**:

- References one Temporal execution when `workflowId` is present.
- Submit events correspond to one Temporal update request.

## Failure Reason

**Purpose**: Normalized reason used by telemetry and operator-facing failure states.

**Values**:

- `unsupported_workflow_type`
- `missing_capability`
- `temporal_task_editing_disabled`
- `state_not_eligible`
- `stale_state`
- `artifact_missing`
- `artifact_malformed`
- `artifact_preparation_failed`
- `validation`
- `backend_rejected`
- `unknown`

**Validation Rules**:

- Reasons must be actionable enough for rollout debugging.
- Reasons must not embed unbounded backend exception text; detailed messages may be shown to operators separately when safe.

## Regression Scenario

**Purpose**: Repeatable coverage case proving one supported flow or explicit failure mode.

**Fields**:

- `name`: Scenario name.
- `mode`: `edit`, `rerun`, or `route`.
- `executionState`: Active, terminal, unsupported, stale, or malformed fixture state.
- `expectedAction`: Visible action, blocked render, accepted submit, rejected submit, or no submit.
- `expectedTelemetry`: Events that must be recorded or explicitly absent.
- `expectedNavigation`: Temporal detail redirect, no redirect, or no navigation.

**Validation Rules**:

- Every Phase 5 scenario from the spec must have at least one automated regression scenario.
- Failure scenarios must assert no misleading success redirect.
- Queue-era route or terminology usage must be asserted absent from primary runtime flows.

## Primary Runtime Flow

**Purpose**: The active operator-facing Temporal task editing path.

**Fields**:

- `entrySurface`: Task detail page.
- `route`: Shared task form route with either edit or rerun execution identity.
- `mode`: `edit` or `rerun`.
- `submitUpdateName`: `UpdateInputs` or `RequestRerun`.
- `successDestination`: Temporal execution detail context.
- `copyFamily`: Active edit copy or terminal rerun copy.

**Validation Rules**:

- Must not use `/tasks/queue/new`, `editJobId`, queue update routes, or queue resubmit wording.
- Must distinguish active in-place editing from terminal rerun.
- Must return to a Temporal detail context after success.

## Rollout Stage

**Purpose**: Controlled exposure level for `temporalTaskEditing`.

**States**:

- `local_development`
- `staging`
- `internal_dogfood`
- `limited_production`
- `all_operators`
- `disabled`

**State Transitions**:

```text
disabled -> local_development -> staging -> internal_dogfood -> limited_production -> all_operators
all stages -> disabled
limited_production -> internal_dogfood
internal_dogfood -> staging
```

**Validation Rules**:

- Expansion requires acceptable failure rates and support feedback.
- Rollback must hide entry points without routing through queue-era fallback.
- Local and staging enablement must be possible through runtime configuration.
