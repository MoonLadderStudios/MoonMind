# Tasks: Define Canonical Task-Shaped Contract and Server-Side Normalization

**Feature**: 330-define-canonical-task-contract (MM-638)
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Summary

- **Total tasks**: 21
- **Parallelizable**: T003, T004, T005 (new types independent of each other); T009, T010, T011 (unit tests within phase)
- **Independent test criteria**: Submit canonical `task`-typed payloads with/without recovery fields to `build_canonical_task_view` (unit) and the executions API (integration) and confirm valid payloads are accepted and normalized, while malformed payloads produce explicit `TaskContractError` messages

## Dependency Graph

```
T001 (setup, verify read)
  └─► T002 [red-first: write failing unit tests]
        └─► T003 [TaskRecoveryKind]   ─┐
        └─► T004 [TaskRecoveryProv.]   ├─► T006 [TaskExecutionSpec fields]
        └─► T005 [ResumeRef]          ─┘
                                            └─► T007 [cross-validator]
T008 [TaskGitSelection.branch] ────────► independent (no deps on T003-T005)
T009-T012 [unit tests: complete] ──────► depends on T003-T008
T013 [__all__ update] ─────────────────► depends on T003-T005
T014 [run unit tests: all pass] ───────► depends on T009-T013
T015-T018 [integration tests] ─────────► depends on T014
T019 [run integration tests] ──────────► depends on T015-T018
T020 [story validation] ───────────────► depends on T014, T019
T021 [/moonspec-verify] ───────────────► depends on T020
```

---

## Phase 1: Setup

- [ ] T001 Read `moonmind/workflows/tasks/task_contract.py` (lines 1–100) to confirm insertion point for new types and current `__all__` list

---

## Phase 2: Red-First Unit Tests (write before implementation)

Write failing tests covering all FRs before touching implementation. Tests must fail (red) at this point.

- [ ] T002 [P] [US1] Write skeleton failing tests in `tests/unit/workflows/tasks/test_task_contract.py` that import `TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef` from `moonmind.workflows.tasks.task_contract` and assert the cross-validation rules — confirm `ImportError` or `AssertionError` proves red-first baseline

---

## Phase 3: New Contract Types (FR-001, FR-002, FR-003)

- [ ] T003 [P] [US1] Add `TaskRecoveryKind = Literal[...]` type alias to `moonmind/workflows/tasks/task_contract.py` after the `_SELF_MANAGED_PUBLISH_SKILLS` block
- [ ] T004 [P] [US1] Add `TaskRecoveryProvenance(BaseModel)` to `moonmind/workflows/tasks/task_contract.py` with required `kind`, `sourceWorkflowId`, `sourceRunId` and optional `requestedBy`, `requestedAt`; field validators enforce non-empty required strings
- [ ] T005 [P] [US1] Add `ResumeFromFailedStepRef(BaseModel)` to `moonmind/workflows/tasks/task_contract.py` with required `kind` (literal), `sourceWorkflowId`, `sourceRunId`, `failedStepId`, `resumeCheckpointRef`, `taskInputSnapshotRef` and optional `failedStepAttempt`, `planRef`, `planDigest`; field validators enforce non-empty required strings

---

## Phase 4: `TaskGitSelection` Update (FR-010, FR-011)

- [ ] T006 [US1] Add `branch: str | None = Field(None, alias="branch")` to `TaskGitSelection` in `moonmind/workflows/tasks/task_contract.py`; add `@model_validator(mode="before")` that maps incoming `targetBranch` to `branch` when `branch` is absent, so canonical output never carries `targetBranch`

---

## Phase 5: `TaskExecutionSpec` Fields and Cross-Validation (FR-004 through FR-009, FR-012)

- [ ] T007 [US1] Add `recovery: TaskRecoveryProvenance | None`, `resume: ResumeFromFailedStepRef | None`, and `depends_on: list[str] | None` (alias `dependsOn`) fields to `TaskExecutionSpec` in `moonmind/workflows/tasks/task_contract.py`
- [ ] T008 [US1] Add `@field_validator("depends_on", mode="before")` to `TaskExecutionSpec` that normalizes `None` and `[]` to `None`, passes non-empty lists through verbatim
- [ ] T009 [US1] Add `@model_validator(mode="after")` `_validate_recovery_resume_consistency` to `TaskExecutionSpec` enforcing: (a) `resume_from_failed_step` + no `resume` → error; (b) `resume` present + `recovery.kind != "resume_from_failed_step"` → error; (c) `resume` present + no `recovery` → error; (d) `exact_full_rerun`/`edited_full_retry` + `resume` present → error

---

## Phase 6: `__all__` Update (FR-013 traceability)

- [ ] T010 [US1] Add `TaskRecoveryKind`, `TaskRecoveryProvenance`, `ResumeFromFailedStepRef` to `__all__` in `moonmind/workflows/tasks/task_contract.py`

---

## Phase 7: Complete Unit Tests (all FRs — green)

- [ ] T011 [P] [US1] Add/complete tests for FR-001/002/003 in `tests/unit/workflows/tasks/test_task_contract.py`: `TaskRecoveryKind` rejects invalid literals; `TaskRecoveryProvenance` requires non-empty `sourceWorkflowId`/`sourceRunId`; `ResumeFromFailedStepRef` requires non-empty required fields including `resumeCheckpointRef`
- [ ] T012 [P] [US1] Add/complete tests for FR-004/005 in `tests/unit/workflows/tasks/test_task_contract.py`: `TaskExecutionSpec` accepts `recovery` and `resume` as optional fields; absence of both does not affect plain task payloads
- [ ] T013 [P] [US1] Add/complete tests for FR-006/007 cross-validation in `tests/unit/workflows/tasks/test_task_contract.py`: `resume_from_failed_step` without `resume` block → `TaskContractError`; `resume` block without matching `recovery.kind` → `TaskContractError`; `resume` block with no `recovery` → `TaskContractError`
- [ ] T014 [P] [US1] Add/complete tests for FR-008 in `tests/unit/workflows/tasks/test_task_contract.py`: `exact_full_rerun` and `edited_full_retry` with valid `sourceWorkflowId`/`sourceRunId` accepted; either without source IDs → error; either with `resume` block → error
- [ ] T015 [P] [US1] Add/complete tests for FR-009/010/011 in `tests/unit/workflows/tasks/test_task_contract.py`: `dependsOn` list preserved verbatim; empty list normalized to `None`; `task.git.targetBranch` absent from canonical output; `task.git.branch` present in output
- [ ] T016 [US1] Run `./tools/test_unit.sh` (or `MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/unit/workflows/tasks/test_task_contract.py -v`) and confirm all new and existing tests pass

---

## Phase 8: Integration Tests (FR-012 executions API boundary)

These are hermetic `integration_ci` tests that exercise the executions API normalization path using `build_canonical_task_view` directly (no external credentials required).

- [ ] T017 [US1] Add `@pytest.mark.integration` `@pytest.mark.integration_ci` test in `tests/integration/` (e.g. `tests/integration/api/test_task_contract_normalization.py`) that calls `build_canonical_task_view` with a well-formed `resume_from_failed_step` payload and asserts the normalized output preserves all recovery and resume fields (SC-001)
- [ ] T018 [US1] Add `@pytest.mark.integration` `@pytest.mark.integration_ci` test that calls `build_canonical_task_view` with `recovery.kind = "resume_from_failed_step"` and absent `resume` block and asserts `TaskContractError` is raised with an operator-readable message (SC-002)
- [ ] T019 [US1] Add `@pytest.mark.integration` `@pytest.mark.integration_ci` test that calls `build_canonical_task_view` with a `resume` block present but `recovery.kind != "resume_from_failed_step"` and asserts `TaskContractError` is raised (SC-003)
- [ ] T020 [US1] Add `@pytest.mark.integration` `@pytest.mark.integration_ci` test that calls `build_canonical_task_view` with `task.git.targetBranch` in a canonical task payload and asserts the normalized output carries `task.git.branch` and does not carry `targetBranch` (SC-006)
- [ ] T021 [US1] Run `./tools/test_integration.sh` and confirm all new `integration_ci` tests pass

---

## Phase 9: Story Validation

Verify all acceptance scenarios from spec.md pass without requiring downstream workflow inspection.

- [ ] T022 [US1] Confirm acceptance scenario 1: well-formed `resume_from_failed_step` payload accepted and all fields preserved in normalized output (SC-001)
- [ ] T023 [US1] Confirm acceptance scenario 2: `resume_from_failed_step` with absent/incomplete `task.resume` block produces explicit validation error (SC-002)
- [ ] T024 [US1] Confirm acceptance scenario 3: `task.resume` block with `recovery.kind != "resume_from_failed_step"` produces explicit validation error (SC-003)
- [ ] T025 [US1] Confirm acceptance scenarios 4–5: `exact_full_rerun` and `edited_full_retry` accepted; recovery fields preserved; no `resume` block required (SC-004)
- [ ] T026 [US1] Confirm acceptance scenario 7: `task.dependsOn` list preserved verbatim in normalized output (SC-005)
- [ ] T027 [US1] Confirm acceptance scenario 6: `task.git.targetBranch` in canonical payload produces normalized output with `branch` only (SC-006)
- [ ] T028 [US1] Confirm acceptance scenario 8: empty `resumeCheckpointRef` after normalization is rejected with explicit error (SC-002/SC-001 edge case)

---

## Phase 10: Final Verification

- [ ] T029 [US1] Run `/moonspec-verify` against spec `330-define-canonical-task-contract` and confirm all success criteria SC-001 through SC-007 are satisfied; attach or record verification output as an artifact

---

## Implementation Notes

- All source changes are within `task_contract.py` and `test_task_contract.py` — no other source files need modification
- Integration tests in Phase 8 call `build_canonical_task_view` directly; they do not require compose networking or external API endpoints and qualify as hermetic `integration_ci`
- Legacy `codex_exec` / `codex_skill` builder functions (`_build_task_from_codex_exec_payload`, `_build_task_from_codex_skill_payload`) continue to use `targetBranch` in their internal output dicts; this is intentional and not a contract violation because those are non-canonical legacy paths
- The new `_validate_recovery_resume_consistency` validator must run after all field validators so that `self.recovery` and `self.resume` are already parsed models (not raw dicts)
- `extra="allow"` on `TaskExecutionSpec` means unknown fields are preserved; `recovery` and `resume` must be declared fields (not relying on extra) so validators fire correctly
