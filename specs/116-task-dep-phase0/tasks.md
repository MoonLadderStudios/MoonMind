# Tasks: Task Dependencies Phase 0 — Spec Alignment

**Input**: Design documents from `specs/116-task-dep-phase0/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No project initialization needed — this is a documentation-only phase in an existing monorepo.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Verify the canonical doc is already aligned before updating the plan tracker.

- [X] T001 [US1] Read and audit `docs/Tasks/TaskDependencies.md` against all Phase 0 requirements from `docs/tmp/011-TaskDependenciesPlan.md` — confirm all terminology, snapshot, and v1 scope checks pass

**Checkpoint**: Audit complete. Canonical doc verified as aligned.

---

## Phase 3: User Story 1 - Verify documentation alignment (Priority: P1) 🎯 MVP

**Goal**: Confirm `docs/Tasks/TaskDependencies.md` satisfies all Phase 0 requirements. No content edits are expected based on research findings; record any discovered gaps as an issue comment.

**Independent Test**: Read the document and verify all FR-001 through FR-005 checks pass.

### Validation for User Story 1

- [X] T002 [US1] Verify `docs/Tasks/TaskDependencies.md` uses `workflowId` as dependency target identifier (FR-001)
- [X] T003 [P] [US1] Verify `docs/Tasks/TaskDependencies.md` states `taskId == workflowId` for Temporal-backed surfaces (FR-002)
- [X] T004 [P] [US1] Verify `docs/Tasks/TaskDependencies.md` states `dependsOn` flows via `initialParameters.task.dependsOn` (FR-003)
- [X] T005 [P] [US1] Verify `docs/Tasks/TaskDependencies.md` contains an implementation snapshot section listing implemented vs. missing features (FR-004)
- [X] T006 [P] [US1] Verify `docs/Tasks/TaskDependencies.md` states v1 scope constraints: create-time only, MoonMind.Run only, max 10 deps, no edit, no cross-type (FR-005)

**Checkpoint**: US1 complete — canonical doc verified as aligned with all Phase 0 requirements.

---

## Phase 4: User Story 2 - Update plan tracking doc (Priority: P2)

**Goal**: Mark Phase 0 as complete in `docs/tmp/011-TaskDependenciesPlan.md` so downstream phases can be identified as next up.

**Independent Test**: Read `docs/tmp/011-TaskDependenciesPlan.md` to confirm Phase 0 is marked complete while Phases 1–5 remain open.

### Implementation for User Story 2

- [X] T007 [US2] Update `docs/tmp/011-TaskDependenciesPlan.md` to mark Phase 0 as complete (FR-006, FR-007)

### Validation for User Story 2

- [X] T008 [US2] Confirm `docs/tmp/011-TaskDependenciesPlan.md` Phase 0 entry shows completed status and Phases 1–5 remain open (FR-007)

**Checkpoint**: US2 complete — plan tracking doc reflects Phase 0 completion.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Final regression gate.

- [X] T009 Run `./tools/test_unit.sh` to confirm no regressions (FR-001 through FR-007, SC-004) — 2162 passed, exit 0

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 2)**: No dependencies — start immediately
- **US1 (Phase 3)**: Depends on Phase 2 (audit required before sub-checks)
- **US2 (Phase 4)**: Independent of US1 but logically sequenced after
- **Polish (Phase 5)**: Depends on all phases complete

### Parallel Opportunities

- T002–T006 are independent verification checks on the same read-only document; T003–T006 can run in parallel

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Audit (T001)
2. Complete Phase 3: Verification checks (T002–T006)
3. **STOP and VALIDATE**: Document is verified
4. Complete Phase 4: Plan doc update (T007–T008)
5. Phase 5: Regression gate

### Incremental Delivery

1. Phase 2 → Foundation (audit)
2. US1 → Verify all Phase 0 requirements are met
3. US2 → Update tracker
4. Polish → Regression gate

---

## Notes

- All tasks reference specific FR IDs for full traceability
- [P] tasks = verifications that can be done in parallel (read-only checks)
- Total tasks: 9
- Tasks per story: US1=6, US2=2, Polish=1
- All tasks complete: 2162 unit tests passed, exit code 0
