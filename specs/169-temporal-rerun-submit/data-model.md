# Data Model: Temporal Rerun Submit

## Temporal Execution

Represents the source execution used to reconstruct a rerun draft and validate whether rerun is allowed.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `workflowId` | string | Yes | Stable identifier for the source execution. |
| `workflowType` | string | Yes | Initial supported value is `MoonMind.Run`. |
| `state` | string | Yes | Lifecycle state; rerun is intended for terminal executions exposed as rerunnable by backend capability flags. |
| `inputParameters` | object | No | Inline structured inputs from the original or latest execution state. |
| `inputArtifactRef` | string or null | No | Historical input artifact reference, if the original/latest input was artifact-backed. |
| `actions.canRerun` | boolean | Yes | Authoritative rerun capability gate for the UI. |
| `actions.canUpdateInputs` | boolean | Yes | Used to preserve edit vs rerun distinction. |

### Validation Rules

- `workflowType` must be `MoonMind.Run` for this feature.
- `actions.canRerun` must be true before rendering a submittable rerun form.
- Rerun mode must not reinterpret `actions.canUpdateInputs` as rerun permission.
- Unsupported workflow types or missing capability must produce explicit error states.

## Rerun Draft

Represents the operator-reviewable shared form state reconstructed from execution data and artifacts.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `runtime` | string or null | No | Runtime value prefilled when reconstructable. |
| `providerProfile` | string or null | No | Provider profile prefilled when reconstructable. |
| `model` | string or null | No | Model value prefilled when reconstructable. |
| `effort` | string or null | No | Effort value prefilled when reconstructable. |
| `repository` | string or null | No | Repository prefilled from execution or artifact input. |
| `branch` | string or null | No | Single authored branch selection prefilled when reconstructable. |
| `publishMode` | string or null | No | Publish mode prefilled when available. It remains part of rerun submission semantics even when rendered inline with Branch. |
| `taskInstructions` | string | Yes | Operator-visible task instructions reconstructed from inline inputs or artifact content. |
| `primarySkill` | string or null | No | Primary skill/template selection when reconstructable. |
| `appliedTemplates` | array | No | Template state preserved for review when present. |

### Validation Rules

- `taskInstructions` is required; rerun submission must be blocked when instructions cannot be reconstructed.
- Artifact-backed inputs must be loaded before building a submittable draft when inline instructions are absent.
- Incomplete or malformed artifact content must not produce a misleading partial rerun form.
- Rerun drafts surface only `branch`, never `targetBranch`.
- Rerun submission emits the new single-branch contract and must not depend on legacy `targetBranch`.

### Legacy Branch Rerun Behavior

Older executions may contain `startingBranch` and `targetBranch`.

| Legacy shape | Rerun behavior |
| --- | --- |
| `startingBranch` only | Normalize to `branch`. |
| `startingBranch == targetBranch` | Normalize the shared value to `branch`. |
| PR publish snapshot with differing values | Normalize `branch` to old `startingBranch`, because it represented the PR base. Old `targetBranch` is retained only as historical metadata. |
| Branch-publish snapshot with differing values | Show a user-facing warning that the previous two-branch intent cannot be preserved exactly. Rerun uses the normalized single `branch` only after operator review. |

Rerun must not use `targetBranch` as executable intent under the new model.

## Rerun Request

Represents the submitted Temporal rerun intent.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `updateName` | string | Yes | Must be `RequestRerun`. |
| `parametersPatch` | object | Yes | Structured task input values reviewed or modified by the operator. |
| `inputArtifactRef` | string | No | New replacement input artifact reference when content is externalized or source was artifact-backed. |
| `idempotencyKey` | string | No | May be supplied by caller/backend contract when available. |

### Validation Rules

- `updateName` must be exactly `RequestRerun` in rerun mode.
- Rerun mode must not submit `UpdateInputs`.
- Rerun mode must not submit to task creation or queue-era endpoints.
- If artifact preparation fails, no rerun request is submitted.

## Input Artifact Reference

Represents immutable task input content.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `artifactId` | string | Yes | Identifier for newly created rerun input content. |
| `sourceWorkflowId` | string | No | Useful lineage context for the execution that produced the rerun request. |
| `content` | object | Yes | Task input payload stored for future reconstruction. |

### Validation Rules

- Historical artifact content must not be mutated.
- New rerun input content must use a replacement artifact reference when externalized.
- The replacement artifact reference must be included in the rerun request when created.

## Rerun Lineage

Represents the operator-visible relationship between the source execution, replacement inputs, and latest run context.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `sourceWorkflowId` | string | Yes | The execution used as the rerun source. |
| `replacementInputArtifactRef` | string or null | No | New artifact reference created for rerun input content. |
| `resultWorkflowId` | string or null | No | Returned workflow identifier or source workflow identifier when unchanged. |
| `applied` | string | No | Backend-reported application mode, such as continue-as-new or accepted. |
| `message` | string or null | No | Operator-facing backend result message. |

### Validation Rules

- Success handling must preserve source context even when the result workflow identifier differs.
- A rejected rerun request must not create a success lineage state or redirect.
- Latest-run messaging must distinguish rerun from active in-place edit.
