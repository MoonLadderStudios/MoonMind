# Tasks: Submit Preset Auto-Expansion

**Input**: Design documents from `/specs/295-submit-preset-auto-expansion/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: submit unresolved Create-page Preset steps by auto-expanding them into executable Tool/Skill steps during explicit Create, Update, or Rerun submission.

**Source Traceability**: Tasks reference `FR-*`, acceptance scenarios `SCN-*`, success criteria `SC-*`, and source mappings `DESIGN-REQ-*` from [spec.md](./spec.md).

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/workflows/tasks/test_task_contract.py`
- Integration tests: `./tools/test_integration.sh` if API routes, task contract normalization, artifact handling, or execution submission boundaries change; otherwise integration-style Create-page scenarios run through the focused Vitest command above
- Final verification: `/moonspec-verify` or the repository `/speckit.verify` equivalent

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete work
- Include exact file paths in descriptions
- Include requirement, scenario, success criterion, or source IDs when the task implements or validates behavior

## Source Traceability Summary

| Coverage Group | IDs | Planned Coverage |
| --- | --- | --- |
| Primary submit convenience | FR-001, FR-002, FR-004, FR-008, FR-009, SCN-001, SCN-002, SC-001, SC-003, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-006, DESIGN-REQ-009 | Failing Create-page tests, frozen submit-copy implementation, ordered expansion, executable-only payload validation |
| Non-submit and inference guardrails | FR-003, FR-006, FR-007, SCN-005, DESIGN-REQ-005, DESIGN-REQ-010 | Verification tests first, selected-key expansion request assertions, conditional fallback guardrail implementation |
| Manual Apply continuity and provenance | FR-005, FR-011, FR-012, SCN-003, DESIGN-REQ-004, DESIGN-REQ-008, DESIGN-REQ-012 | Existing manual tests preserved, submit-time payload comparison, provenance assertions |
| Failure, duplicate, stale, and side-effect safety | FR-014, FR-015, FR-018, SCN-004, SCN-007, SC-002, SC-004, DESIGN-REQ-007, DESIGN-REQ-011 | Failing tests for no side effects, draft preservation, request identity, stale/cancel handling |
| Attachments and publish/merge constraints | FR-013, FR-016, SCN-006, SC-005, DESIGN-REQ-007, DESIGN-REQ-009 | Failing tests for ambiguous retargeting blocks and publish/merge constraint handling |
| Authoritative validation | FR-017, SC-005, DESIGN-REQ-010 | Existing task-contract rejection retained and re-run in final validation |

## Requirement Status Summary

| Status | Count | Handling |
| --- | ---: | --- |
| missing | 15 | Add failing tests, confirm red, implement code, validate |
| partial | 23 | Add tests for missing behavior, confirm red where behavior is absent, complete implementation |
| implemented_unverified | 3 | Add verification tests first and conditional fallback implementation if those tests fail |
| implemented_verified | 2 | Preserve existing evidence and include final validation only |

## Phase 1: Setup

**Purpose**: Confirm active artifacts and test tooling before story work.

- [ ] T001 [P] Verify active feature inputs and source traceability in `specs/295-submit-preset-auto-expansion/spec.md`, `specs/295-submit-preset-auto-expansion/plan.md`, `specs/295-submit-preset-auto-expansion/research.md`, `specs/295-submit-preset-auto-expansion/data-model.md`, `specs/295-submit-preset-auto-expansion/contracts/create-page-submit-preset-auto-expansion.md`, and `specs/295-submit-preset-auto-expansion/quickstart.md` for FR-001 through FR-019 and DESIGN-REQ-001 through DESIGN-REQ-012.
- [ ] T002 [P] Confirm frontend and Python focused test commands are available by checking `package.json`, `tools/test_unit.sh`, and `tools/test_integration.sh` for the commands listed in `specs/295-submit-preset-auto-expansion/quickstart.md`.

---

## Phase 2: Foundational

**Purpose**: Prepare reusable test fixtures and identify stable implementation seams before story implementation.

**CRITICAL**: No production story implementation starts until Phase 2 is complete.

- [ ] T003 [P] Add or refactor reusable Preset expansion fetch mocks and request-capture helpers in `frontend/src/entrypoints/task-create.test.tsx` for FR-001, FR-006, FR-012, SCN-001, SCN-003, DESIGN-REQ-005, and DESIGN-REQ-008.
- [ ] T004 [P] Identify the production seam for submit-copy expansion in `frontend/src/entrypoints/task-create.tsx`, preserving existing manual `expandPresetForDraft` and `applyPresetExpansionToDraft` behavior for FR-005, FR-011, and DESIGN-REQ-004.

**Checkpoint**: Test fixtures and implementation seam are ready. Story test authoring can begin.

---

## Phase 3: Story - Submit Draft With Unresolved Presets

**Summary**: As a Create-page user, I want unresolved Preset steps to expand automatically when I explicitly submit the task so that I can create, update, or rerun tasks without manually previewing and applying trusted presets first.

**Independent Test**: Author a Create-page draft with unresolved Preset steps, click the primary create/update/rerun action, and verify the resulting submission contains only executable Tool/Skill steps with Preset provenance, or no submission side effect occurs when expansion cannot complete safely.

**Traceability**: FR-001 through FR-019; SCN-001 through SCN-007; SC-001 through SC-005; DESIGN-REQ-001 through DESIGN-REQ-012

**Unit Test Plan**:

- Vitest coverage in `frontend/src/entrypoints/task-create.test.tsx` for submit-time expansion helpers, selected-key behavior, stale response handling, draft preservation, and manual Apply continuity.
- Pytest coverage in `tests/unit/workflows/tasks/test_task_contract.py` to preserve authoritative rejection of unresolved Preset payloads.

**Integration Test Plan**:

- Integration-style Create-page Vitest scenarios covering successful Create, Update/Rerun, failed expansion with no side effect, ordered multi-Preset expansion, and final executable-only payload shape.
- Run `./tools/test_integration.sh` only if implementation changes API, artifact, task contract, or execution submission boundaries.

### Unit Tests (write first)

> Write these tests first. Run them and confirm they fail for the expected reason before production implementation, except tasks explicitly marked as verification for existing behavior.

- [ ] T005 Add failing Vitest test for successful Create submit with one unresolved Preset auto-expanding to executable Tool/Skill steps in `frontend/src/entrypoints/task-create.test.tsx` covering FR-001, FR-002, FR-004, FR-005, FR-012, SCN-001, SCN-003, SC-001, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-008.
- [ ] T006 Add failing Vitest test for Update or Rerun submit using the same auto-expansion path in `frontend/src/entrypoints/task-create.test.tsx` covering FR-001, FR-002, FR-018, SCN-001, SCN-007, and SC-005.
- [ ] T007 Add failing Vitest test for three unresolved Preset steps expanding in authored order into the frozen submission copy in `frontend/src/entrypoints/task-create.test.tsx` covering FR-008, FR-009, SCN-002, SC-003, DESIGN-REQ-006, and DESIGN-REQ-009.
- [ ] T008 Add failing Vitest test that expansion failure, authorization failure, or invalid Preset input blocks final submission, creates no side effect, marks the relevant Preset step, and preserves the visible draft in `frontend/src/entrypoints/task-create.test.tsx` covering FR-014, FR-018, SCN-004, SC-002, DESIGN-REQ-011.
- [ ] T009 Add failing Vitest test for duplicate submit clicks and stale expansion responses during submit-time expansion in `frontend/src/entrypoints/task-create.test.tsx` covering FR-015, SC-004, DESIGN-REQ-007, and DESIGN-REQ-010.
- [ ] T010 Add failing Vitest test for attachment retargeting ambiguity blocking auto-submission and preserving manual review path in `frontend/src/entrypoints/task-create.test.tsx` covering FR-016, SCN-006, SC-005, and DESIGN-REQ-007.
- [ ] T011 Add failing Vitest test for publish/merge constraints and returned capabilities/warnings applying before final payload validation in `frontend/src/entrypoints/task-create.test.tsx` covering FR-013, SCN-003, SCN-006, DESIGN-REQ-007, and DESIGN-REQ-009.
- [ ] T012 Add verification Vitest test that Preset selection, descriptor loading, Jira import, attachment upload, manual Preview, and navigation do not auto-expand or submit before a primary submit click in `frontend/src/entrypoints/task-create.test.tsx` covering FR-003, FR-007, SCN-005, DESIGN-REQ-005, and DESIGN-REQ-010.
- [ ] T013 [P] Add or preserve pytest regression in `tests/unit/workflows/tasks/test_task_contract.py` proving unresolved `type: "preset"` steps are rejected and flat Tool/Skill steps with Preset provenance are accepted for FR-017, FR-012, DESIGN-REQ-008, and DESIGN-REQ-010.

### Integration Tests (write first)

- [ ] T014 Add integration-style Create-page Vitest scenario for full create payload inspection after submit-time expansion in `frontend/src/entrypoints/task-create.test.tsx` covering SCN-001, SCN-003, SC-001, FR-004, FR-012, DESIGN-REQ-001, and DESIGN-REQ-008.
- [ ] T015 Add integration-style Create-page Vitest scenario for failed expansion producing no `/api/executions` or edit/rerun update request in `frontend/src/entrypoints/task-create.test.tsx` covering SCN-004, SC-002, FR-014, FR-018, and DESIGN-REQ-011.
- [ ] T016 Add integration-style Create-page Vitest scenario for create/update/rerun parity or document why the existing edit/rerun test harness only supports one of update or rerun in `frontend/src/entrypoints/task-create.test.tsx` covering SCN-001, SCN-007, FR-001, FR-002, and SC-005.

### Red-First Confirmation

- [ ] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T005-T011 and T014-T016 fail for the expected missing submit-time expansion behavior while T012 documents current verification status.
- [ ] T018 [P] Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/workflows/tasks/test_task_contract.py` and confirm T013 passes or fails only for an intended contract regression before production changes.

### Conditional Fallback for Implemented-Unverified Rows

- [ ] T019 If T012 fails because non-submit interactions already create side effects, add guardrail fixes in `frontend/src/entrypoints/task-create.tsx` for FR-003, FR-007, SCN-005, DESIGN-REQ-005, and DESIGN-REQ-010 before continuing with submit expansion implementation.

### Implementation

- [ ] T020 Add transient submit-expansion state and request identity handling to `frontend/src/entrypoints/task-create.tsx` for FR-010, FR-015, DESIGN-REQ-003, DESIGN-REQ-007, and DESIGN-REQ-010.
- [ ] T021 Refactor Preset expansion request building in `frontend/src/entrypoints/task-create.tsx` so manual Preview/Apply and submit-time expansion share selected key, version, inputs, context, warnings, capabilities, and provenance semantics for FR-005, FR-006, FR-007, FR-011, FR-012, DESIGN-REQ-004, DESIGN-REQ-005, and DESIGN-REQ-012.
- [ ] T022 Implement frozen submission-copy creation and authored-order unresolved Preset replacement in `frontend/src/entrypoints/task-create.tsx` for FR-001, FR-002, FR-004, FR-008, FR-009, SCN-001, SCN-002, SC-001, SC-003, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-006, and DESIGN-REQ-009.
- [ ] T023 Implement executable-only final payload validation and stale incompatible field cleanup for auto-expanded steps in `frontend/src/entrypoints/task-create.tsx` for FR-004, FR-012, FR-017, DESIGN-REQ-008, and DESIGN-REQ-010.
- [ ] T024 Implement submit-time failure handling, Preset-scoped error feedback, final-submission-failure draft preservation, duplicate-click guard behavior, and stale response ignoring in `frontend/src/entrypoints/task-create.tsx` for FR-014, FR-015, FR-018, SCN-004, SCN-007, SC-002, SC-004, DESIGN-REQ-007, DESIGN-REQ-010, and DESIGN-REQ-011.
- [ ] T025 Implement attachment-ref availability checks, ambiguous retargeting block behavior, publish/merge constraint application, warning handling, and required capability propagation for submit-time expansion in `frontend/src/entrypoints/task-create.tsx` for FR-013, FR-016, SCN-003, SCN-006, SC-005, DESIGN-REQ-007, and DESIGN-REQ-009.
- [ ] T026 Preserve existing manual Preview/Apply behavior and applied-preset executable submission behavior in `frontend/src/entrypoints/task-create.tsx` for FR-005, FR-011, FR-012, DESIGN-REQ-004, and DESIGN-REQ-012.

### Story Validation

- [ ] T027 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix failures in `frontend/src/entrypoints/task-create.tsx` or `frontend/src/entrypoints/task-create.test.tsx` until all Create-page tests pass for FR-001 through FR-019 and SCN-001 through SCN-007.
- [ ] T028 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only -- tests/unit/workflows/tasks/test_task_contract.py` and fix failures in `moonmind/workflows/tasks/task_contract.py` or `tests/unit/workflows/tasks/test_task_contract.py` only if authoritative task validation regressed for FR-017.
- [ ] T029 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and fix story-related failures in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, `moonmind/workflows/tasks/task_contract.py`, or `tests/unit/workflows/tasks/test_task_contract.py`.
- [ ] T030 Run `./tools/test_integration.sh` if T020-T026 changed API, artifact, task contract normalization, or execution submission boundaries; otherwise record in `specs/295-submit-preset-auto-expansion/quickstart.md` or final notes that focused frontend integration-style coverage is the required integration evidence for this UI-only implementation.

**Checkpoint**: The story is fully functional, covered by unit and integration evidence, and independently testable.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T031 [P] Update `docs/UI/CreatePage.md` only if implementation discovers a contract mismatch; otherwise leave canonical docs unchanged and preserve source-design traceability for DESIGN-REQ-001 through DESIGN-REQ-012.
- [ ] T032 [P] Review `frontend/src/entrypoints/task-create.tsx` for localized refactoring after tests pass, avoiding unrelated Create-page rewrites and preserving manual Preview/Apply behavior for FR-011.
- [ ] T033 Run the end-to-end acceptance checklist in `specs/295-submit-preset-auto-expansion/quickstart.md` and record any blocked command or environment prerequisite in final implementation notes.
- [ ] T034 Run `/moonspec-verify` or the repository `/speckit.verify` equivalent after implementation and all required tests pass, validating the completed branch against the original request in `specs/295-submit-preset-auto-expansion/spec.md`.

---

## Dependencies and Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Phase 1 and blocks story test/implementation work.
- **Story (Phase 3)**: Depends on Phase 2. Unit and integration tests must be written before implementation.
- **Polish and Verification (Phase 4)**: Depends on story implementation and tests passing.

### Within The Story

- T005-T013 must be written before T017-T018.
- T014-T016 must be written before T017.
- T017-T018 red-first confirmation must complete before T020-T026 production work.
- T019 runs only if T012 exposes a current non-submit side-effect gap.
- T020-T021 should precede T022 because frozen submit-copy expansion depends on transient state and shared expansion request semantics.
- T022 should precede T023-T025 because final validation, failure handling, attachments, and constraints depend on expanded submission-copy construction.
- T026 runs after core submit-time changes to preserve manual Preview/Apply behavior.
- T027-T030 validate the story before Phase 4.

### Parallel Opportunities

- T001 and T002 can run in parallel.
- T003 and T004 can run in parallel after Phase 1 because they prepare tests and implementation seams in different files or disjoint concerns.
- T013 can be authored in parallel with frontend test tasks T005-T012 because it touches `tests/unit/workflows/tasks/test_task_contract.py`.
- T018 can run in parallel with T017 after tests are written because it targets Python contract tests.
- T031 can run in parallel with T032 after all story tests pass if documentation and code review are independent.

---

## Parallel Example: Story Phase

```bash
# After Phase 2, split frontend and Python validation work:
Task: "T005-T012 add failing Create-page submit-time expansion tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "T013 add or preserve task contract regression in tests/unit/workflows/tasks/test_task_contract.py"

# After implementation, validate separate suites:
Task: "T027 run focused Create-page Vitest validation"
Task: "T028 run focused task-contract pytest validation"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 setup checks.
2. Complete Phase 2 test fixture and implementation seam preparation.
3. Write T005-T016 tests first.
4. Run T017-T018 and confirm missing/partial behavior fails red for the expected reason while existing verified contract behavior remains green.
5. Run T019 only if verification for FR-003 or FR-007 exposes a pre-existing side-effect or inference gap.
6. Implement T020-T026 in `frontend/src/entrypoints/task-create.tsx` with narrowly scoped changes.
7. Run T027-T030 until required unit and integration evidence passes.
8. Complete Phase 4 polish, quickstart validation, and `/moonspec-verify` or the repository `/speckit.verify` equivalent.

### Status Handling

- `missing` and `partial` rows receive failing tests plus implementation tasks.
- `implemented_unverified` rows FR-003, FR-007, and SCN-005 receive verification tests plus conditional fallback task T019.
- `implemented_verified` rows FR-011 and FR-017 receive preservation/final validation tasks and no new implementation unless regressions appear.

---

## Notes

- This task list covers one story only.
- Do not introduce linked-live Preset runtime execution.
- Do not allow unresolved Preset steps in final task payloads.
- Do not mutate checked-in skill folders.
- Preserve unrelated worktree changes.
- Commit only when explicitly requested by the user or the execution environment.
