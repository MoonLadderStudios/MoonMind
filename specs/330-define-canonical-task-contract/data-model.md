# Data Model: Define Canonical Task-Shaped Contract and Server-Side Normalization

**Feature**: 330-define-canonical-task-contract
**Phase**: 1 — Design
**Created**: 2026-05-08

## New Types

### `TaskRecoveryKind`

A three-value discriminated literal classifying which recovery workflow was requested.

```python
TaskRecoveryKind = Literal["exact_full_rerun", "edited_full_retry", "resume_from_failed_step"]
```

**Valid values**:
- `"exact_full_rerun"` — retry the whole task with the original input unchanged
- `"edited_full_retry"` — retry the whole task after the user edited the input
- `"resume_from_failed_step"` — retry only the last failed step, importing completed prior progress

**Validation**: any value outside these three is rejected with `TaskContractError`.

---

### `TaskRecoveryProvenance`

A structured record attached to a task submission identifying the recovery intent and source run.

```python
class TaskRecoveryProvenance(BaseModel):
    kind: TaskRecoveryKind                  # required; discriminated recovery intent
    source_workflow_id: str                 # alias: sourceWorkflowId; required, non-empty
    source_run_id: str                      # alias: sourceRunId; required, non-empty
    requested_by: str | None = None         # alias: requestedBy; optional
    requested_at: str | None = None         # alias: requestedAt; optional
```

**Validation rules**:
- `kind` must be one of the three canonical literals (enforced by `TaskRecoveryKind` type)
- `sourceWorkflowId` must be a non-empty string
- `sourceRunId` must be a non-empty string
- `requestedBy` and `requestedAt` are optional; their absence does not cause validation failure

---

### `ResumeFromFailedStepRef`

A structured record pinning a resume submission to a specific source run, failed step, resume checkpoint, and task input snapshot.

```python
class ResumeFromFailedStepRef(BaseModel):
    kind: Literal["resume_from_failed_step"]  # required; discriminating literal
    source_workflow_id: str                   # alias: sourceWorkflowId; required, non-empty
    source_run_id: str                        # alias: sourceRunId; required, non-empty
    failed_step_id: str                       # alias: failedStepId; required, non-empty
    failed_step_attempt: int | None = None    # alias: failedStepAttempt; optional
    resume_checkpoint_ref: str                # alias: resumeCheckpointRef; required, non-empty
    task_input_snapshot_ref: str              # alias: taskInputSnapshotRef; required, non-empty
    plan_ref: str | None = None               # alias: planRef; optional
    plan_digest: str | None = None            # alias: planDigest; optional
```

**Validation rules**:
- `kind` must be the literal `"resume_from_failed_step"`
- `sourceWorkflowId`, `sourceRunId`, `failedStepId`, `resumeCheckpointRef`, `taskInputSnapshotRef` must all be non-empty strings
- `failedStepAttempt`, `planRef`, `planDigest` are optional; their absence does not cause validation failure

---

## Modified Types

### `TaskGitSelection` — add `branch` field

```python
class TaskGitSelection(BaseModel):
    branch: str | None = None           # alias: branch; canonical authored branch field (NEW)
    starting_branch: str | None = None  # alias: startingBranch; checkout ref / source SHA (UNCHANGED)
    # target_branch removed from canonical authored contract; legacy paths continue internally
```

**Normalization rule**: if a canonical `task`-typed payload supplies `task.git.targetBranch`, it is mapped to `branch` during model validation so the canonical normalized output contains `branch`, not `targetBranch`. The legacy `codex_exec` / `codex_skill` builder functions remain unchanged (they use `targetBranch` internally for their own output shape, which is acceptable for those non-canonical paths).

---

### `TaskExecutionSpec` — add `recovery`, `resume`, `dependsOn` fields

```python
class TaskExecutionSpec(BaseModel):
    # ... existing fields unchanged ...
    recovery: TaskRecoveryProvenance | None = None  # alias: recovery (NEW)
    resume: ResumeFromFailedStepRef | None = None   # alias: resume (NEW)
    depends_on: list[str] | None = None             # alias: dependsOn (NEW)
```

**Cross-validation rules** (enforced by a new `@model_validator(mode="after")`):

| recovery.kind | resume present? | Result |
|---|---|---|
| `"resume_from_failed_step"` | Yes (valid) | ACCEPTED |
| `"resume_from_failed_step"` | No / missing required fields | REJECTED — explicit error |
| `"exact_full_rerun"` | Yes | REJECTED — ambiguous intent |
| `"edited_full_retry"` | Yes | REJECTED — ambiguous intent |
| `None` (absent) | Yes | REJECTED — resume without recovery kind |
| Any | Neither | ACCEPTED (no recovery intent) |

**`dependsOn` normalization**:
- `None` or absent → stored as `None`
- `[]` (empty list) → normalized to `None`
- Non-empty list → preserved verbatim (strings are opaque identifiers)

---

## Entity Relationships

```
TaskExecutionSpec
  ├── recovery?: TaskRecoveryProvenance
  │     └── kind: TaskRecoveryKind ("exact_full_rerun" | "edited_full_retry" | "resume_from_failed_step")
  ├── resume?: ResumeFromFailedStepRef
  │     └── kind: Literal["resume_from_failed_step"]
  ├── dependsOn?: list[str]
  └── git: TaskGitSelection
        └── branch?: str  (new canonical authored field)
```

**Invariant**: `resume` is only valid when `recovery.kind == "resume_from_failed_step"`. The two fields are always validated together; one without the other is always an error.

---

## Affected Source Files

| File | Change |
|------|--------|
| `moonmind/workflows/tasks/task_contract.py` | Add `TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef`; update `TaskGitSelection` and `TaskExecutionSpec`; update `__all__` |
| `tests/unit/workflows/tasks/test_task_contract.py` | Add unit tests for all new types, fields, and validation rules (FR-001 through FR-013) |
