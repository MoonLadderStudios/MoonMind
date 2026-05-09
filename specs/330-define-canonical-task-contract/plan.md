# Implementation Plan: Define Canonical Task-Shaped Contract and Server-Side Normalization

**Branch**: `run-jira-orchestrate-for-mm-638-define-c-282ddc9a` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/330-define-canonical-task-contract/spec.md`
**Jira**: MM-638

## Summary

Extend `moonmind/workflows/tasks/task_contract.py` with three new Pydantic models (`TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef`), two new fields on `TaskExecutionSpec` (`recovery`, `resume`), one new field (`dependsOn`), and the canonical authored branch field (`branch`) on `TaskGitSelection`. Add a cross-validation model validator that enforces the recovery/resume pairing invariants specified in `docs/Tasks/TaskArchitecture.md` §6 and §11. Cover all acceptance scenarios from MM-638 with unit tests in `test_task_contract.py`.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, existing MoonMind task contract module
**Storage**: N/A — no new persistent storage; validation output only
**Unit Testing**: pytest (`./tools/test_unit.sh`)
**Integration Testing**: pytest with Docker (`./tools/test_integration.sh`); existing hermetic CI suite
**Target Platform**: Linux server (worker container)
**Project Type**: library — internal Pydantic model and validation layer
**Performance Goals**: Validation must complete in O(1) field checks with no external I/O
**Constraints**: All changes are backward-compatible for non-recovery payloads; no new database tables; legacy `codex_exec` / `codex_skill` paths unchanged
**Scale/Scope**: Single Python module, ~1800 LOC existing; adding ~100 LOC new types + ~50 LOC validators + ~200 LOC tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I — Orchestrate, Don't Recreate | PASS | Strengthens the orchestration contract; does not add a new cognitive engine |
| II — One-Click Deployment | PASS | No deploy changes; pure Python contract extension |
| III — Avoid Vendor Lock-In | PASS | Recovery contract is platform-neutral |
| IV — Own Your Data | PASS | Contract fields are stored in operator-controlled infrastructure |
| V — Skills Are First-Class | PASS | No skill contract changes |
| VI — Bittersweet Lesson | PASS | Thin contract layer behind stable interface; design for deletion |
| VII — Powerful Runtime Configurability | PASS | No hardcoded behavior; all values are validated inputs |
| VIII — Modular and Extensible | PASS | Changes isolated to `task_contract.py`; no core orchestration changes |
| IX — Resilient by Default | PASS | Explicit validation errors instead of silent inference (Invariant 13) |
| X — Facilitate Continuous Improvement | PASS | N/A to this story |
| XI — Spec-Driven Development | PASS | Spec exists and is quality-checked |
| XII — Canonical Docs | PASS | `docs/Tasks/TaskArchitecture.md` is the source; spec artifacts are in `specs/330-*` |
| XIII — Pre-Release Velocity | PASS | `targetBranch` removed from canonical authored contract; no compatibility shim |

**Post-design re-check**: All principles still PASS. No violations to document.

## Project Structure

### Documentation (this feature)

```text
specs/330-define-canonical-task-contract/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # Quality gate (already passing)
└── tasks.md             # Phase 2 output
```

### Source Code (repository root)

```text
moonmind/workflows/tasks/
└── task_contract.py          # All contract changes (new types + updated models + validators)

tests/unit/workflows/tasks/
└── test_task_contract.py     # New unit tests for FR-001 through FR-013
```

**Structure Decision**: Single-module contract library; no new files required. All changes are additive within `task_contract.py` plus tests.

## Implementation Strategy

### Phase 1: New contract types (FR-001, FR-002, FR-003)

Add three new types to `task_contract.py` immediately after the existing `_SELF_MANAGED_PUBLISH_SKILLS` block:

1. `TaskRecoveryKind` — `Literal["exact_full_rerun", "edited_full_retry", "resume_from_failed_step"]`
2. `TaskRecoveryProvenance(BaseModel)` — `kind`, `sourceWorkflowId`, `sourceRunId`, optional `requestedBy`/`requestedAt`; validators ensure `sourceWorkflowId` and `sourceRunId` are non-empty
3. `ResumeFromFailedStepRef(BaseModel)` — `kind` (literal `"resume_from_failed_step"`), `sourceWorkflowId`, `sourceRunId`, `failedStepId`, optional `failedStepAttempt`, `resumeCheckpointRef`, `taskInputSnapshotRef`, optional `planRef`/`planDigest`; validators ensure all required fields are non-empty

### Phase 2: `TaskGitSelection` update (FR-010, FR-011)

Add `branch: str | None` (alias `"branch"`) as the canonical authored branch field. Add a `@model_validator(mode="before")` that normalizes incoming `targetBranch` to `branch` when present, so the canonical output always carries `branch`. Keep `starting_branch` (alias `startingBranch`) unchanged.

**Note**: The `_build_task_from_codex_exec_payload` and `_build_task_from_codex_skill_payload` functions produce `{"targetBranch": None, ...}` in their internal dict output — these are non-canonical legacy paths and are not affected.

### Phase 3: `TaskExecutionSpec` fields and cross-validation (FR-004 through FR-009, FR-012)

Add to `TaskExecutionSpec`:
- `recovery: TaskRecoveryProvenance | None = Field(None, alias="recovery")`
- `resume: ResumeFromFailedStepRef | None = Field(None, alias="resume")`
- `depends_on: list[str] | None = Field(None, alias="dependsOn")`

Add a `@model_validator(mode="after")` `_validate_recovery_resume_consistency` that enforces:
- `recovery.kind == "resume_from_failed_step"` → `resume` must be present
- `resume` present → `recovery` must exist and `recovery.kind` must be `"resume_from_failed_step"`
- `recovery.kind in {"exact_full_rerun", "edited_full_retry"}` → `resume` must be absent

Add a `@field_validator("depends_on", mode="before")` that normalizes `None` and `[]` to `None`.

### Phase 4: `__all__` and export update (FR-013 traceability)

Add `TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef` to `__all__`.

### Phase 5: Unit tests (all FRs)

Add tests to `tests/unit/workflows/tasks/test_task_contract.py` covering:
- FR-001/002/003: Type instantiation, required field validation, optional field acceptance
- FR-004/005: `recovery` and `resume` fields accepted on `TaskExecutionSpec`
- FR-006: `resume_from_failed_step` without `resume` block → validation error
- FR-007: `resume` block without matching `recovery.kind` → validation error
- FR-008: `exact_full_rerun` and `edited_full_retry` with and without source IDs
- FR-009: `dependsOn` accepted, preserved verbatim; empty list normalized to None
- FR-010/011: `task.git.branch` canonical field; `targetBranch` normalized to `branch`
- Edge cases from spec: plain task (no recovery) unaffected; `resumeCheckpointRef` empty → error

## Complexity Tracking

No Constitution Check violations. No complexity notes required.
