---
description: "Task list for Liquid Glass Publish Panel"
---

# Tasks: Liquid Glass Publish Panel

**Input**: Design documents from `/specs/210-liquid-glass-panel/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `contracts/liquid-glass-panel.md`, `quickstart.md`

**Tests**: Unit tests and integration-style UI tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around one independently testable story: the Create Page bottom publish/action panel presents a liquid glass blur/refraction treatment while preserving controls, readability, responsiveness, and create behavior.

**Source Traceability**: Original Jira reference and preset brief are preserved in `spec.md`. This task list maps all `FR-*`, acceptance scenarios, edge cases, and `SC-*` evidence to concrete test, implementation, and verification work.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches a different file or does not depend on incomplete edits.
- Include exact file paths in descriptions.
- Include requirement, scenario, or source IDs when the task implements or validates behavior.

## Phase 1: Setup

**Purpose**: Confirm the active feature context and existing UI/test surfaces before story work.

- [ ] T001 Confirm active feature artifacts exist for FR-010/SC-006 in `specs/210-liquid-glass-panel/spec.md`, `specs/210-liquid-glass-panel/plan.md`, `specs/210-liquid-glass-panel/research.md`, `specs/210-liquid-glass-panel/contracts/liquid-glass-panel.md`, and `specs/210-liquid-glass-panel/quickstart.md`
- [ ] T002 Inspect the current bottom task submission controls in `frontend/src/entrypoints/task-create.tsx` and record the stable selectors/accessible names to use in tests for FR-002/FR-003
- [ ] T003 Inspect the current `.queue-floating-bar` and `.queue-floating-bar-row` styling in `frontend/src/styles/mission-control.css` to identify the minimum liquid glass changes for FR-001/FR-006/FR-007/FR-008

---

## Phase 2: Foundational

**Purpose**: Establish the test targets and contract boundaries that block story implementation.

**CRITICAL**: No production styling changes until the red-first test tasks and confirmation tasks are complete.

- [ ] T004 Identify or add stable test access paths in `frontend/src/entrypoints/task-create.test.tsx` for the task submission controls group without changing production code behavior, covering FR-002 and the UI contract surface
- [ ] T005 Confirm existing Create page submission request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` cover repository, branch, publish mode, and create behavior for FR-004/FR-005/SC-004/SC-005

**Checkpoint**: Test targets and contract boundaries are ready.

---

## Phase 3: Story - Liquid Glass Publish Panel

**Summary**: As a Mission Control task author, I want the bottom Create Page panel that contains repository, branch, publish mode, and create controls to use a liquid glass blur and refraction treatment so that the publishing controls feel visually polished while remaining easy to use.

**Independent Test**: Open the Create Page with the bottom publish/action panel visible, inspect the repository, branch, publish mode, and create controls across desktop and mobile widths, and submit a valid task draft. The story passes when the panel has a liquid glass blur and refraction treatment, all controls remain readable and usable, the layout stays stable, and task creation behavior is unchanged.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006

**Unit Test Plan**:

- Verify the bottom submission controls group is the styled panel target.
- Verify the liquid glass treatment is attached to that target through stable class/style assertions.
- Verify accessible controls remain present for GitHub Repo, Branch, Publish Mode, and Create.
- Verify layout state classes remain stable for normal and branch-status states.

**Integration Test Plan**:

- Preserve existing Create page request-shape tests for valid draft submission.
- Verify repository, branch, and publish mode interaction still produce the expected task submission semantics.
- Use quickstart visual checks for desktop/mobile and light/dark readability because visual blur/refraction cannot be fully proven by DOM tests alone.

### Unit Tests (write first)

- [ ] T006 [P] Add failing unit test in `frontend/src/entrypoints/task-create.test.tsx` asserting the task submission controls group exposes the liquid glass panel treatment for FR-001/FR-002/FR-009/SC-001
- [ ] T007 [P] Add failing unit test in `frontend/src/entrypoints/task-create.test.tsx` asserting GitHub Repo, Branch, Publish Mode, and Create controls remain accessible inside the treated panel for FR-003/SC-002
- [ ] T008 [P] Add failing unit test in `frontend/src/entrypoints/task-create.test.tsx` asserting long repository/branch values, branch status, disabled, and loading states keep the same panel target and stable control layout for FR-006/FR-007/SC-003
- [ ] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T006-T008 fail for the expected missing liquid-glass/readability assertions before production changes

### Integration Tests (write first)

- [ ] T010 [P] Add or extend integration-style UI test in `frontend/src/entrypoints/task-create.test.tsx` proving a valid draft submission still includes existing repository, branch, publish mode, and create semantics for FR-004/FR-005/SC-004/SC-005
- [ ] T011 [P] Add or extend integration-style UI test in `frontend/src/entrypoints/task-create.test.tsx` proving the treated panel still contains the same controls after repository, branch, and publish mode interactions for acceptance scenarios 2, 3, and 4
- [ ] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T010-T011 fail only where new verification expects unimplemented panel treatment, not because existing create behavior is broken

### Implementation

- [ ] T013 Implement the liquid glass blur/refraction panel treatment in `frontend/src/styles/mission-control.css` for FR-001/FR-008/SC-001/SC-002 while preserving fallback readability
- [ ] T014 Refine `.queue-floating-bar-row` responsive and stable sizing rules in `frontend/src/styles/mission-control.css` only if red-first tests or quickstart checks expose overlap, clipping, or layout shift for FR-006/FR-007/SC-003
- [ ] T015 Update `frontend/src/entrypoints/task-create.tsx` only if tests need a stable semantic hook for the contracted task submission controls group, preserving existing control labels and submit behavior for FR-002/FR-003/FR-004/FR-005
- [ ] T016 Update `docs/UI/CreatePage.md` only if the desired-state documentation lacks the liquid glass panel treatment requirement for FR-009/SC-006, keeping implementation notes out of canonical docs
- [ ] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix failures until the story-specific unit and integration-style UI tests pass for FR-001 through FR-009

### Story Validation

- [ ] T018 Execute the visual checks in `specs/210-liquid-glass-panel/quickstart.md` for desktop and mobile widths, light and dark appearance, long repository/branch values, branch unavailable/loading states, publish-mode constraints, readability, layout fit, and unchanged create behavior for SC-001/SC-002/SC-003/SC-004/SC-005
- [ ] T019 Confirm the supplied Jira issue reference and original preset brief remain present in `specs/210-liquid-glass-panel/spec.md`, `specs/210-liquid-glass-panel/tasks.md`, implementation notes, and final verification evidence for FR-010/SC-006

**Checkpoint**: The single story is implemented, covered by unit and integration-style UI tests, visually checked, and traceable to the original brief.

---

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T020 [P] Review `frontend/src/styles/mission-control.css` for unrelated palette drift, excessive one-off styling, and fallback readability after the liquid glass changes for FR-008
- [ ] T021 [P] Review `frontend/src/entrypoints/task-create.test.tsx` for focused assertions that do not over-constrain implementation details beyond the UI contract in `specs/210-liquid-glass-panel/contracts/liquid-glass-panel.md`
- [ ] T022 Run full unit verification with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- [ ] T023 Run `/speckit.verify` for `specs/210-liquid-glass-panel/spec.md` after implementation and tests pass

---

## Dependencies and Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Phase 1 and blocks story work.
- **Story (Phase 3)**: Depends on Phase 2.
- **Polish and Verification (Phase 4)**: Depends on Phase 3 tests and story validation.

### Within The Story

- T006-T008 must be written before T009.
- T010-T011 must be written before T012.
- T009 and T012 must confirm red-first behavior before T013-T016.
- T013 is the primary production styling task.
- T014 and T015 are conditional implementation tasks only if tests or visual checks expose gaps.
- T017 must pass before T018-T019.
- T022 must pass before T023.

### Parallel Opportunities

- T006, T007, and T008 can be authored in parallel if coordinated within `frontend/src/entrypoints/task-create.test.tsx`.
- T010 and T011 can be authored in parallel if coordinated within `frontend/src/entrypoints/task-create.test.tsx`.
- T020 and T021 can run in parallel because they review different files.

---

## Parallel Example

```bash
# After Phase 2, test authoring can be split by concern:
Task: "Add failing unit test for liquid glass panel treatment in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing integration-style UI test for preserved Create page submission semantics in frontend/src/entrypoints/task-create.test.tsx"
```

---

## Implementation Strategy

1. Confirm feature artifacts and current UI/style surfaces.
2. Write focused failing tests for the liquid glass panel treatment, accessibility/readability, layout stability, and create behavior preservation.
3. Confirm the new tests fail for the expected missing treatment or missing assertions.
4. Implement the minimum CSS changes in `frontend/src/styles/mission-control.css`.
5. Touch `frontend/src/entrypoints/task-create.tsx` only if stable semantic hooks are needed for tests or accessibility.
6. Preserve task submission payload semantics and rerun focused Create page tests.
7. Complete quickstart visual checks.
8. Run full unit verification.
9. Run final `/speckit.verify`.

---

## Coverage Matrix

| Requirement | Tasks |
| --- | --- |
| FR-001 | T006, T009, T013, T017, T018 |
| FR-002 | T002, T004, T006, T015, T017 |
| FR-003 | T007, T013, T015, T017, T018 |
| FR-004 | T010, T011, T015, T017 |
| FR-005 | T005, T010, T017 |
| FR-006 | T008, T014, T017, T018 |
| FR-007 | T008, T014, T018 |
| FR-008 | T013, T018, T020 |
| FR-009 | T006, T007, T008, T010, T011, T018 |
| FR-010 | T001, T019, T023 |
| SC-001 | T006, T013, T018 |
| SC-002 | T007, T013, T018 |
| SC-003 | T008, T014, T018 |
| SC-004 | T010, T011, T017 |
| SC-005 | T010, T017, T018 |
| SC-006 | T019, T023 |

## Notes

- This task list covers exactly one story.
- No `data-model.md` tasks are required because the story adds no data entities, persistence, or state transitions.
- No compose-backed integration suite is required; Create page request-shape and interaction tests provide the integration-style boundary for this UI story.
- Commit messages and pull request metadata must preserve the supplied Jira issue reference text from `spec.md`.
