# Tasks: Shared Additive Shimmer Masks

**Input**: Design documents from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/status-pill-shimmer.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. This task list is generated after implementation evidence already exists, so test tasks are verification-first and red-first replay tasks document how the same tests must fail against a pre-implementation baseline.

**Organization**: Tasks cover exactly one story: Phase-Locked Additive Shimmer.

**Source Traceability**: Covers FR-001 through FR-008, SCN-001 through SCN-006, SC-001 through SC-005, DESIGN-REQ-001 through DESIGN-REQ-005, and the original request preserved in `spec.md`.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when it touches different files and has no dependency on another incomplete task.
- Every task includes exact file paths and requirement, scenario, success criterion, or design IDs where applicable.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing frontend test and documentation surfaces are ready for one-story verification.

- [X] T001 Verify the active feature artifact set exists in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/spec.md`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/plan.md`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/research.md`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/data-model.md`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/contracts/status-pill-shimmer.md`, and `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/quickstart.md`
- [X] T002 Confirm the spec contains exactly one `## User Story -` section and no unresolved clarification markers in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/spec.md`
- [X] T003 [P] Confirm frontend dependencies and local binaries needed by quickstart commands exist or can be prepared by `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/tools/test_unit.sh`
- [X] T004 [P] Confirm npm-script PATH fallback guidance is documented for managed workspaces in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/quickstart.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm the existing UI surfaces and contracts that block story verification.

**CRITICAL**: No story validation work should begin until this phase confirms the target files and contracts are present.

- [X] T005 Verify the shared status metadata boundary remains `executionStatusPillProps()` in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/utils/executionStatusPillClasses.ts` for FR-003, FR-008, and DESIGN-REQ-005
- [X] T006 Verify active label rendering still flows through `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/components/ExecutionStatusPill.tsx` for FR-002 and FR-008
- [X] T007 Verify the shared shimmer visual contract remains centralized in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/styles/mission-control.css` for FR-001 through FR-006 and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004
- [X] T008 Verify the canonical desired-state documentation remains in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/docs/UI/EffectShimmerSweep.md` for FR-007 and DESIGN-REQ-003

**Checkpoint**: Foundation ready. Story tests and implementation evidence validation can proceed.

---

## Phase 3: Story - Phase-Locked Additive Shimmer

**Summary**: As a Mission Control user, I want executing and planning status pills to show one coherent shimmer light passing through fill, border, and text so active progress looks physical and intentional.

**Independent Test**: Render active and inactive status pills, inspect the documented effect contract and stylesheet rules, and verify that only active shimmer pills expose one shared moving light field through fill, border, and text masks, with reduced-motion and unsupported text-mask fallbacks.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005

**Requirement Status Summary**: All rows are `implemented_verified` in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/plan.md`; tasks therefore preserve evidence and final validation instead of adding default production work.

**Test Plan**:

- Unit: CSS contract tests in `frontend/src/entrypoints/mission-control.test.tsx` cover shared light field, fill mask, border mask, text mask, reduced motion, forced colors, fallback glyph brightening, and CSS-only animation.
- Integration: Testing Library render tests in `frontend/src/entrypoints/tasks-list.test.tsx` and `frontend/src/entrypoints/task-detail.test.tsx` cover list, card, and detail active status-pill surfaces and non-active isolation.

### Unit Tests (verify first)

- [X] T009 [P] Verify unit CSS contract coverage for FR-001, FR-002, FR-004, FR-005, FR-006, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-004 in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/entrypoints/mission-control.test.tsx`
- [X] T010 [P] Verify non-active state isolation and selector boundary coverage for FR-003, FR-008, SCN-006, and DESIGN-REQ-005 in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/utils/executionStatusPillClasses.test.ts`
- [X] T011 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/utils/executionStatusPillClasses.test.ts` from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo` and record unit verification evidence for FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, SC-003, and SC-004

### Integration Tests (verify first)

- [X] T012 [P] Verify task-list table and card render coverage for active status metadata, labels, glyph spans, and non-active isolation in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/entrypoints/tasks-list.test.tsx` for FR-003, FR-008, SCN-002, SCN-006, and DESIGN-REQ-005
- [X] T013 [P] Verify task-detail render coverage for active status metadata, labels, glyph spans, and non-active isolation in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/entrypoints/task-detail.test.tsx` for FR-003, FR-008, SCN-002, SCN-006, and DESIGN-REQ-005
- [X] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx` from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo` and record integration verification evidence for list, card, and detail surfaces

### Red-First Confirmation

- [X] T015 Confirm red-first provenance for FR-001, FR-002, FR-004, FR-005, FR-006, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SC-001, SC-002, SC-003, and SC-004 by documenting that the assertions in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/entrypoints/mission-control.test.tsx` would fail against the pre-shared-light-field implementation
- [X] T016 Confirm red-first provenance for FR-003, FR-008, SCN-002, SCN-006, and DESIGN-REQ-005 by documenting that the assertions in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/entrypoints/tasks-list.test.tsx`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/entrypoints/task-detail.test.tsx`, and `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/utils/executionStatusPillClasses.test.ts` would fail if active shimmer metadata or glyph label rendering regressed

### Implementation Evidence

- [X] T017 Verify shared light-field implementation evidence for FR-001, FR-002, FR-004, FR-005, FR-006, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-004 in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/styles/mission-control.css`
- [X] T018 Verify active label implementation evidence for FR-002 and FR-008 in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/components/ExecutionStatusPill.tsx`
- [X] T019 Verify selector-boundary implementation evidence for FR-003, FR-008, and DESIGN-REQ-005 in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/utils/executionStatusPillClasses.ts`
- [X] T020 Verify documentation implementation evidence for FR-007, SC-005, and DESIGN-REQ-003 in `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/docs/UI/EffectShimmerSweep.md`
- [X] T021 If any verification task T009 through T020 fails, update the specific failing file among `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/styles/mission-control.css`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/components/ExecutionStatusPill.tsx`, `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/frontend/src/utils/executionStatusPillClasses.ts`, or `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/docs/UI/EffectShimmerSweep.md` only enough to restore the mapped FR, SCN, SC, or DESIGN-REQ evidence

### Story Validation

- [X] T022 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only` from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo` and confirm full dashboard coverage remains green for FR-001 through FR-008
- [X] T023 Run `node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo` and confirm TypeScript validation remains green for FR-008
- [X] T024 Run `node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src` from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo` and confirm lint validation remains green for the touched frontend files

**Checkpoint**: The one story is fully validated by unit evidence, integration evidence, implementation evidence, typecheck, lint, and documentation evidence.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding scope.

- [X] T025 [P] Confirm `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/plan.md` still marks only evidence-backed `implemented_verified` rows and does not hide missing work for FR-001 through FR-008
- [X] T026 [P] Confirm `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/contracts/status-pill-shimmer.md` maps the host, shared light-field, text, fallback, and verification contracts to existing tests
- [X] T027 [P] Confirm `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer/quickstart.md` contains the focused unit, focused integration, full dashboard, typecheck, lint, and manual validation commands
- [X] T028 Run `git diff --check` from `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo` before final verification
- [X] T029 Run `/speckit.verify` for `/work/agent_jobs/mm:0fc1399c-3b5a-43d5-abe4-f58413039e6c/repo/specs/301-shared-additive-shimmer` after implementation evidence and tests pass, and record the final verdict in the verification artifact

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story validation.
- **Story (Phase 3)**: Depends on Phase 2; unit/integration verification and red-first provenance precede implementation evidence sign-off.
- **Polish (Phase 4)**: Depends on the story checkpoint.

### Within The Story

- Unit verification tasks T009 through T011 precede implementation evidence tasks T017 through T021.
- Integration verification tasks T012 through T014 precede implementation evidence tasks T017 through T021.
- Red-first provenance tasks T015 and T016 precede implementation evidence tasks T017 through T021.
- Conditional implementation task T021 runs only if verification tasks expose a regression or evidence gap.
- Story validation tasks T022 through T024 run after implementation evidence tasks T017 through T021.
- Final `/speckit.verify` task T029 runs after T022 through T028 pass.

### Parallel Opportunities

- T003 and T004 can run in parallel after T001 and T002.
- T009 and T010 can run in parallel because they inspect different test files.
- T012 and T013 can run in parallel because they inspect different entrypoint test files.
- T017, T018, T019, and T020 can run in parallel because they inspect different implementation or documentation files.
- T025, T026, and T027 can run in parallel because they inspect different MoonSpec artifacts.

---

## Parallel Example: Story Phase

```bash
# Parallel verification review examples:
Task: "T009 Verify CSS contract coverage in frontend/src/entrypoints/mission-control.test.tsx"
Task: "T012 Verify task-list render coverage in frontend/src/entrypoints/tasks-list.test.tsx"
Task: "T013 Verify task-detail render coverage in frontend/src/entrypoints/task-detail.test.tsx"

# Parallel implementation evidence review examples:
Task: "T017 Verify shared light-field implementation evidence in frontend/src/styles/mission-control.css"
Task: "T018 Verify active label implementation evidence in frontend/src/components/ExecutionStatusPill.tsx"
Task: "T020 Verify documentation implementation evidence in docs/UI/EffectShimmerSweep.md"
```

---

## Implementation Strategy

### Verification-First Story Delivery

1. Complete Phase 1 to confirm the one-story artifact set and tooling.
2. Complete Phase 2 to confirm shared status-pill contracts and target files.
3. Execute unit and integration verification tasks before signing off implementation evidence.
4. Record red-first provenance for the existing tests because all plan rows are `implemented_verified`.
5. Run conditional implementation task T021 only if verification exposes a gap.
6. Run full dashboard, typecheck, lint, and quickstart validation.
7. Run final `/speckit.verify` against the original request preserved in `spec.md`.

### Requirement Status Handling

- Code-and-test work: 0 rows, because the plan has no `missing` or `partial` rows.
- Verification-only work: 0 rows, because the plan has no `implemented_unverified` rows.
- Conditional fallback implementation work: T021 only if implemented-verified evidence regresses during execution.
- Already-verified rows: FR-001 through FR-008, SCN-001 through SCN-006, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-005.

---

## Notes

- This task list covers exactly one story and must not be expanded into unrelated Mission Control visual work.
- Browser capability fallback is part of this story; internal compatibility aliases or hidden legacy paths are not.
- Do not modify `.agents/skills` active snapshot projection while executing this task list.
- Commit after each logical task group when tasks are executed by `/speckit.implement`.
