# Research: Define Canonical Task-Shaped Contract and Server-Side Normalization

**Feature**: 330-define-canonical-task-contract
**Phase**: 0 — Research
**Created**: 2026-05-08

## Findings

### Current state of `task_contract.py`

`moonmind/workflows/tasks/task_contract.py` already defines:
- `TaskExecutionSpec` — the core task body model with `instructions`, `skill`, `skills`, `runtime`, `git`, `publish`, `steps`, `inputAttachments`, `container`, `proposalPolicy`, `authoredPresets`
- `TaskGitSelection` — has `starting_branch` (alias `startingBranch`) and `target_branch` (alias `targetBranch`)
- `TaskStepSource`, `AuthoredPresetBinding`, `CanonicalTaskPayload`, `build_canonical_task_view`, `normalize_queue_job_payload`

**Gaps confirmed against spec requirements**:
- `TaskRecoveryKind` — absent; not defined anywhere in codebase
- `TaskRecoveryProvenance` — absent
- `ResumeFromFailedStepRef` — absent
- `TaskExecutionSpec.recovery` — absent
- `TaskExecutionSpec.resume` — absent
- `TaskExecutionSpec.dependsOn` — absent
- `TaskGitSelection.branch` (canonical authored branch) — absent; currently only `targetBranch` (legacy) and `startingBranch` (checkout ref)
- Cross-validation of recovery/resume pairing — absent

### `TaskGitSelection` field semantics

Decision: Add `branch` as the new canonical authored field. Keep `starting_branch` unchanged (it is the internal checkout ref / source SHA). The legacy `target_branch` field (alias `targetBranch`) is used by `codex_exec` and `codex_skill` legacy paths internally but must not appear as a first-class field in canonical `task`-typed payloads. Normalization strategy: when a canonical `task` payload includes `task.git.targetBranch`, map it to `branch` silently (same as `_lift_legacy_task_shape` pattern for `targetRuntime`). This satisfies FR-011 without breaking downstream reads of existing data.

Rationale: Pre-release policy (Constitution Principle XIII) permits clean removal of `targetBranch` from the canonical authored contract without a compatibility window.

### Recovery/resume cross-validation placement

Decision: add a `@model_validator(mode="after")` on `TaskExecutionSpec` that enforces:
1. If `recovery.kind == "resume_from_failed_step"` → `resume` must be present and non-None
2. If `resume` is present → `recovery` must exist and `recovery.kind` must be `"resume_from_failed_step"`
3. If `recovery.kind` in `{"exact_full_rerun", "edited_full_retry"}` → `resume` must be absent

The individual model-level validators on `TaskRecoveryProvenance` enforce that `sourceWorkflowId` and `sourceRunId` are non-empty. `ResumeFromFailedStepRef` validators enforce that its own required fields are non-empty.

### `dependsOn` normalization

Decision: `dependsOn` is stored as `list[str]` on `TaskExecutionSpec`. An empty list is treated as absent (normalized to `None`) per the spec edge-case rule. Strings are passed through verbatim with no length or format validation in this story (opaque identifiers).

### API boundary enforcement

`build_canonical_task_view` already calls `CanonicalTaskPayload.model_validate(source)` for canonical `task`-typed payloads, which invokes all Pydantic validators including new cross-validators. Validation errors surface as `TaskContractError` which callers in `executions.py` convert to API-level rejections. No new API boundary wiring is needed — the existing error propagation path covers FR-012.

### Test patterns

Existing `tests/unit/workflows/tasks/test_task_contract.py` (680 lines) uses direct `TaskExecutionSpec.model_validate(...)` and `build_canonical_task_view(...)` calls with `pytest.raises(ValidationError)` / `pytest.raises(TaskContractError)` assertions. New tests follow the same pattern.

`tests/integration/temporal/test_task_shaped_submission_normalization.py` exercises end-to-end submission normalization. Acceptance scenarios from the spec map directly to new unit tests; no new integration test is required to cover the contract-definition story (the existing integration test already exercises normalization at the API boundary).

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| `TaskRecoveryKind` representation | `Literal["exact_full_rerun", "edited_full_retry", "resume_from_failed_step"]` | Discriminated literal matches design doc TypeScript definition |
| `branch` field naming | Add `branch` to `TaskGitSelection`; normalize incoming `targetBranch` to `branch` | Clean break per Constitution XIII; `startingBranch` semantics unchanged |
| `dependsOn` empty list | Normalize `[]` to `None` | Spec edge-case rule: empty list treated same as absent |
| Cross-validation location | `@model_validator(mode="after")` on `TaskExecutionSpec` | Consistent with existing validator placement in the same model |
| New types in `__all__` | Export `TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef` | Allows external consumers to type-check recovery/resume payloads |
