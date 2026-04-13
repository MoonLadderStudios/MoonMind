# Data Model: Temporal Edit UpdateInputs

## Editable Temporal Execution

Represents the active Temporal execution being edited.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `workflowId` | string | Yes | Stable execution identifier used as the editable object. |
| `workflowType` | string | Yes | Must be `MoonMind.Run` for this feature. |
| `state` / `rawState` | string | Yes | Must represent a non-terminal execution for active edit. |
| `actions.canUpdateInputs` | boolean | Yes | Backend-provided edit capability gate. |
| `inputParameters` | object | Yes | Current reconstructed input state used to prefill the shared form. |
| `inputArtifactRef` | string or null | No | Historical input artifact reference, if original input was artifact-backed. |

### Validation Rules

- `workflowType` must be `MoonMind.Run`.
- `actions.canUpdateInputs` must be true before rendering a submittable edit form.
- Terminal states must be rejected at submit time even if the page was loaded while active.

## Edited Task Input State

Represents the operator-reviewed replacement input state produced by the shared form.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `repository` | string | Yes | Repository target for the task. |
| `targetRuntime` | string | Yes | Runtime selected in the shared form. |
| `requiredCapabilities` | string[] | No | Derived from runtime, publish mode, skills, and templates. |
| `task` | object | Yes | Normalized task payload containing instructions and supported task fields. |
| `inputArtifactRef` | string | Conditional | Present when edited input is externalized. |

### Validation Rules

- Required create-form validations still apply to edit submissions for supported fields.
- Unsupported edit/rerun-only controls must not be included as active edit semantics.
- The edited state must be suitable for `parametersPatch`.

## Artifact Edit Update Payload

Represents the payload sent to update the active execution.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `updateName` | string | Yes | Must be `UpdateInputs`. |
| `parametersPatch` | object | Yes | Contains the edited task input state. |
| `inputArtifactRef` | string | Conditional | New artifact reference for edited artifact-backed or oversized input. |
| `idempotencyKey` | string | Optional | May be supplied if the submit flow adds idempotency. |

### Validation Rules

- `updateName` must be exactly `UpdateInputs`.
- `inputArtifactRef`, when present, must reference a newly created artifact for the edited input.
- Historical artifact references must not be reused as edited input references.
- Queue-era identifiers such as `editJobId` must not appear.

## Input Artifact Reference

Represents immutable task input content stored outside the inline request body.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `artifactId` | string | Yes | Identifier returned by artifact creation. |
| `contentType` | string | Yes | JSON task input content for this feature. |
| `metadata.repository` | string or null | No | Repository context for artifact browsing/audit. |
| `metadata.source` | string | Yes | Indicates the submit/edit origin. |

### Validation Rules

- Existing historical artifacts are read-only.
- Edited input artifacts are created before the update request is submitted.
- Artifact creation, upload, and completion failures block edit submission.

## Update Outcome

Represents backend response semantics after an edit submit.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `accepted` | boolean | Yes | Whether the update was accepted. |
| `applied` | string | No | Outcome such as immediate, safe-point/deferred, or continue-as-new style handling. |
| `message` | string | No | Operator-readable backend message. |
| `execution.workflowId` | string | No | Refreshed execution context for redirect when available. |
| `refresh` | object | No | Indicates whether detail/list data should be refetched. |

### State Handling

- Accepted immediate updates produce "saved" success semantics.
- Accepted safe-point/deferred updates produce "scheduled" success semantics.
- Accepted continue-as-new style updates produce "accepted for refreshed run" semantics.
- Rejected updates produce an explicit error and no success redirect.

## Operator Notice State

Represents a one-time message shown after successful navigation back to detail.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `message` | string | Yes | Outcome-specific success text. |
| `scope` | string | Yes | Temporal task editing success notice. |

### Validation Rules

- Notice is shown once after redirect.
- Notice must not be shown after rejected or failed submissions.
