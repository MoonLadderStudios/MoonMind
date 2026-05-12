# Data Model: Backend-Computed Resume Eligibility

Traceability: MM-643, `spec.md` FR-001 through FR-010.

## Recovery Capability State

Represents backend-computed recovery actions for one execution detail response.

Fields:
- `canEditForRerun`: true when the user may open an editable full retry.
- `canRerun`: true when the user may start an exact full rerun.
- `canResumeFromFailedStep`: true when failed-step Resume evidence is complete and valid.
- `disabledReasons`: bounded reason map keyed by action field.

Validation rules:
- Resume availability is computed by the backend.
- Mission Control must render Resume only from `canResumeFromFailedStep`.
- Disabled reasons must be operator-readable and must not include secrets, raw auth data, or full checkpoint payloads.

## Recovery Provenance

Represents the user's accepted recovery intent.

Fields:
- `kind`: one of `exact_full_rerun`, `edited_full_retry`, or `resume_from_failed_step`.
- `sourceWorkflowId`: pinned source workflow id.
- `sourceRunId`: pinned source run id.
- `requestedBy`: optional operator identity.
- `requestedAt`: optional request timestamp.

Validation rules:
- `sourceWorkflowId` and `sourceRunId` are required and non-empty.
- Generic rerun and edited full retry must not be converted into `resume_from_failed_step`.
- Resume must pin both source identifiers so the recovery cannot drift to a later run.

## Failed-Step Resume Reference

Represents the evidence needed for an accepted failed-step Resume request.

Fields:
- `kind`: `resume_from_failed_step`.
- `sourceWorkflowId`
- `sourceRunId`
- `failedStepId`
- `failedStepAttempt`
- `resumeCheckpointRef`
- `taskInputSnapshotRef`
- `planRef`
- `planDigest`

Validation rules:
- Required identifiers and refs must be non-empty.
- At least one plan identity value must be present at the boundary that determines eligibility.
- The resume checkpoint and task input snapshot must correspond to the pinned source workflow/run.
- Edits to task input, attachments, runtime, publish mode, branch, presets, or dependencies are not valid Resume data.

## Resume Evidence Bundle

Logical evidence set used to compute `canResumeFromFailedStep`.

Fields:
- authoritative original task input snapshot
- pinned source workflow id and source run id
- ledger-identified failed step
- completed-step refs for all preserved prior work
- workspace, branch, commit, or equivalent checkpoint
- plan identity or digest
- durable resume checkpoint ref

Validation rules:
- Missing, stale, unauthorized, corrupted, or inconsistent evidence results in unavailable Resume or pre-execution rejection.
- Evidence validation must fail before creating recovery work.
- Large or binary evidence remains behind artifact refs.

## State Transitions

```text
failed execution
  ├─ edit task available -> edited full retry
  ├─ rerun available -> exact full rerun
  └─ resume evidence valid -> resume_from_failed_step accepted
       ├─ evidence invalid before creation -> rejected with reason
       └─ accepted -> linked resumed execution
```

## Relationships

- Failed Execution has one Recovery Capability State.
- Recovery Capability State may reference one Resume Evidence Bundle.
- Accepted Resume has one Recovery Provenance and one Failed-Step Resume Reference.
- Resumed Execution links back to the pinned source workflow/run and evidence bundle.
