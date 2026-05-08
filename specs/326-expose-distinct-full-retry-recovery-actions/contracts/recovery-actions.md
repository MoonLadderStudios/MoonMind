# Contract: Failed Task Recovery Actions

Traceability: MM-632, FR-001 through FR-013.

## Execution Detail Action Contract

Execution detail responses for eligible task workflows expose independent action capability fields:

```json
{
  "workflowId": "mm:source",
  "runId": "run-source",
  "workflowType": "MoonMind.Run",
  "state": "failed",
  "actions": {
    "canEditForRerun": true,
    "canRerun": true,
    "canResumeFromFailedStep": false,
    "disabledReasons": {
      "canResumeFromFailedStep": "resume_checkpoint_missing"
    }
  },
  "resume": {
    "available": false,
    "checkpointRef": null,
    "failedStepId": null,
    "disabledReason": "resume_checkpoint_missing"
  }
}
```

Contract rules:

- `canEditForRerun`, `canRerun`, and `canResumeFromFailedStep` are independent.
- UI must render Edit task only from `canEditForRerun` or active edit capability, Rerun only from `canRerun`, and Resume from failed step only from `canResumeFromFailedStep`.
- Missing or invalid Resume evidence must not disable exact full Rerun or edited full retry when their own prerequisites are satisfied.
- Disabled reasons must be safe to display and must not expose credentials, hidden artifact content, or raw checkpoint bodies.

## Edit Task Contract

Edit task opens the task authoring surface in edit-for-rerun mode.

Required behavior:

- Load authoring fields from the authoritative original task input snapshot.
- Permit normal task input edits subject to normal validation.
- Submit as an edited full retry, not as exact Rerun.
- Create a new authoritative task input snapshot for the new execution.
- Do not import completed source progress.

Invalid behavior:

- Loading editable fields from lossy projections when an authoritative snapshot exists.
- Mutating the failed source execution.
- Importing `resumeCheckpointRef`, `resumeSource`, preserved steps, or prior completed progress.

## Exact Rerun Contract

Exact Rerun starts a full-task retry from the beginning using the original task input unchanged.

Required request semantics:

```json
{
  "action": "rerun",
  "sourceWorkflowId": "mm:source",
  "idempotencyKey": "rerun:mm:source:unique"
}
```

Required behavior:

- Reuse the original task input unchanged.
- Start from the beginning.
- Preserve compact source provenance.
- Do not accept task mutation fields, input artifact overrides, plan artifact overrides, runtime changes, branch changes, dependency changes, preset changes, or Resume checkpoint fields.

Invalid exact Rerun payload fields:

- `task`
- `instructions`
- `steps`
- `attachments`
- `inputAttachments`
- `runtime`
- `targetRuntime`
- `publishMode`
- `branch`
- `startingBranch`
- `targetBranch`
- `presets`
- `dependencies`
- `model`
- `requestedModel`
- `effort`
- `parametersPatch`
- `inputArtifactRef`
- `planArtifactRef`
- `manifestArtifactRef`
- `resumeCheckpointRef`
- `resumeSource`
- `preservedSteps`

Invalid behavior:

- Silently converting exact Rerun with edited fields into Edit task.
- Importing completed progress from the source run.
- Re-executing preserved Resume steps as if they were already completed.

## Resume From Failed Step Contract

Resume from failed step is not an edit or exact Rerun path.

Required behavior:

- Require durable checkpoint evidence.
- Reject edited task/input mutation fields.
- Preserve original task input unchanged.
- Preserve completed prior progress only for the Resume execution.
- Return operator-readable unavailable reasons when checkpoint evidence is missing, stale, unauthorized, or inconsistent.

Invalid behavior:

- Opening authoring UI for Resume.
- Accepting edited task/input fields.
- Falling back to exact Rerun when Resume evidence is invalid.
- Falling back to Resume when a generic Rerun request is made.
