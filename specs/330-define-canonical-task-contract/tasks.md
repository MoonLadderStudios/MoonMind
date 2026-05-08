# Tasks: Define Canonical Task-Shaped Contract and Server-Side Normalization

**Feature**: 330-define-canonical-task-contract (MM-638)
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Summary

- **Total tasks**: 15
- **Parallelizable**: T002, T003, T004 (new types independent of each other)
- **Independent test criteria**: Submit canonical `task`-typed payloads with/without recovery fields to the executions API (via `build_canonical_task_view`) and confirm valid payloads are accepted and normalized, while malformed payloads produce explicit `TaskContractError` messages

## Dependency Graph

```
T001 (setup, verify read)
  └─► T002 [TaskRecoveryKind]   ─┐
  └─► T003 [TaskRecoveryProv.]   ├─► T005 [TaskExecutionSpec fields]
  └─► T004 [ResumeRef]          ─┘
                                      └─► T006 [cross-validator]
T007 [TaskGitSelection.branch] ────────► independent (no deps on T002-T004)
T008-T015 [unit tests] ─────────────► depends on T002-T007
```

---

## Phase 1: Setup

- [ ] T001 Read `moonmind/workflows/tasks/task_contract.py` (lines 1–100) to confirm insertion point for new types and current `__all__` list

---

## Phase 2: New Contract Types (FR-001, FR-002, FR-003)

- [ ] T002 [P] [US1] Add `TaskRecoveryKind = Literal[...]` type alias to `moonmind/workflows/tasks/task_contract.py` after the `_SELF_MANAGED_PUBLISH_SKILLS` block
- [ ] T003 [P] [US1] Add `TaskRecoveryProvenance(BaseModel)` to `moonmind/workflows/tasks/task_contract.py` with required `kind`, `sourceWorkflowId`, `sourceRunId` and optional `requestedBy`, `requestedAt`; field validators enforce non-empty required strings
- [ ] T004 [P] [US1] Add `ResumeFromFailedStepRef(BaseModel)` to `moonmind/workflows/tasks/task_contract.py` with required `kind` (literal), `sourceWorkflowId`, `sourceRunId`, `failedStepId`, `resumeCheckpointRef`, `taskInputSnapshotRef` and optional `failedStepAttempt`, `planRef`, `planDigest`; field validators enforce non-empty required strings

---

## Phase 3: `TaskGitSelection` Update (FR-010, FR-011)

- [ ] T005 [US1] Add `branch: str | None = Field(None, alias="branch")` to `TaskGitSelection` in `moonmind/workflows/tasks/task_contract.py`; add `@model_validator(mode="before")` that maps incoming `targetBranch` to `branch` when `branch` is absent, so canonical output never carries `targetBranch`

---

## Phase 4: `TaskExecutionSpec` Fields and Cross-Validation (FR-004 through FR-009, FR-012)

- [ ] T006 [US1] Add `recovery: TaskRecoveryProvenance | None`, `resume: ResumeFromFailedStepRef | None`, and `depends_on: list[str] | None` (alias `dependsOn`) fields to `TaskExecutionSpec` in `moonmind/workflows/tasks/task_contract.py`
- [ ] T007 [US1] Add `@field_validator("depends_on", mode="before")` to `TaskExecutionSpec` that normalizes `None` and `[]` to `None`, passes non-empty lists through verbatim
- [ ] T008 [US1] Add `@model_validator(mode="after")` `_validate_recovery_resume_consistency` to `TaskExecutionSpec` enforcing: (a) `resume_from_failed_step` + no `resume` → error; (b) `resume` present + `recovery.kind != "resume_from_failed_step"` → error; (c) `resume` present + no `recovery` → error; (d) `exact_full_rerun`/`edited_full_retry` + `resume` present → error

---

## Phase 5: `__all__` Update (FR-013 traceability)

- [ ] T009 [US1] Add `TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef` to `__all__` in `moonmind/workflows/tasks/task_contract.py`

---

## Phase 6: Unit Tests (all FRs)

- [ ] T010 [P] [US1] Add tests for FR-001/002/003 in `tests/unit/workflows/tasks/test_task_contract.py`: `TaskRecoveryKind` rejects invalid literals; `TaskRecoveryProvenance` requires non-empty `sourceWorkflowId`/`sourceRunId`; `ResumeFromFailedStepRef` requires non-empty required fields including `resumeCheckpointRef`
- [ ] T011 [P] [US1] Add tests for FR-004/005 in `tests/unit/workflows/tasks/test_task_contract.py`: `TaskExecutionSpec` accepts `recovery` and `resume` as optional fields; absence of both does not affect plain task payloads
- [ ] T012 [P] [US1] Add tests for FR-006/007 cross-validation in `tests/unit/workflows/tasks/test_task_contract.py`: `resume_from_failed_step` without `resume` block → `TaskContractError`; `resume` block without matching `recovery.kind` → `TaskContractError`; `resume` block with no `recovery` → `TaskContractError`
- [ ] T013 [P] [US1] Add tests for FR-008 in `tests/unit/workflows/tasks/test_task_contract.py`: `exact_full_rerun` and `edited_full_retry` with valid `sourceWorkflowId`/`sourceRunId` accepted; either without source IDs → error; either with `resume` block → error
- [ ] T014 [P] [US1] Add tests for FR-009/010/011 in `tests/unit/workflows/tasks/test_task_contract.py`: `dependsOn` list preserved verbatim; empty list normalized to `None`; `task.git.targetBranch` absent from canonical output; `task.git.branch` present in output
- [ ] T015 [US1] Run `./tools/test_unit.sh` (or `MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/unit/workflows/tasks/test_task_contract.py -v`) and confirm all new and existing tests pass

---

## Polish Phase

No polish tasks required. Cross-cutting concerns (telemetry, UI, downstream workflow enforcement) are explicitly out of scope per spec Assumptions section.

## Implementation Notes

- All changes are within `task_contract.py` and `test_task_contract.py` — no other files need modification
- Legacy `codex_exec` / `codex_skill` builder functions (`_build_task_from_codex_exec_payload`, `_build_task_from_codex_skill_payload`) continue to use `targetBranch` in their internal output dicts; this is intentional and not a contract violation because those are non-canonical legacy paths
- The new `_validate_recovery_resume_consistency` validator must run after all field validators so that `self.recovery` and `self.resume` are already parsed models (not raw dicts)
- `extra="allow"` on `TaskExecutionSpec` means unknown fields are preserved; `recovery` and `resume` must be declared fields (not relying on extra) so validators fire correctly
