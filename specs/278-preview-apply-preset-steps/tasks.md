# Tasks: Preview and Apply Preset Steps

**Input**: Design documents from `/specs/278-preview-apply-preset-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around MM-558's single user story.

**Source Traceability**: FR-001..FR-011, SC-001..SC-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-019.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing Create page preset and test harness boundaries.

- [X] T001 Confirm active MM-558 feature artifacts in `specs/278-preview-apply-preset-steps/spec.md`, `specs/278-preview-apply-preset-steps/plan.md`, `specs/278-preview-apply-preset-steps/research.md`, `specs/278-preview-apply-preset-steps/data-model.md`, `specs/278-preview-apply-preset-steps/contracts/create-page-preset-preview.md`, and `specs/278-preview-apply-preset-steps/quickstart.md`
- [X] T002 Confirm existing Create page implementation and test files in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Reuse existing preset catalog/detail/expand surfaces; no backend schema or service foundation is required unless red tests expose a contract gap.

- [X] T003 Verify existing task-template detail and expand calls in `frontend/src/entrypoints/task-create.tsx` are the authoritative preview/apply source (FR-002, FR-003, FR-004, DESIGN-REQ-017)
- [X] T004 Verify existing generated step mapping in `frontend/src/entrypoints/task-create.tsx` produces editable step state (FR-007)

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - Preview and Apply Preset Steps

**Summary**: As a task author, I want to configure a Preset step, preview its generated steps, and apply it into executable Tool and Skill steps so reusable workflows stay transparent and editable.

**Independent Test**: Render the Create page, choose Step Type `Preset`, select a preset, preview the generated Tool/Skill steps and warnings, apply the preview, and verify the Preset placeholder is replaced by editable concrete steps.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-019

**Test Plan**:

- Unit: preview state, generated step list/warnings, no mutation before apply, apply replacement, unresolved submit block, preview failure handling.
- Integration: Create page Vitest render/submission coverage acts as the story integration boundary because it exercises UI state and mocked task-template API calls.

### Unit Tests (write first) ⚠️

- [X] T005 [P] Add failing test that Step Type `Preset` can preview generated step titles, Step Types, and expansion warnings without mutating the draft in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-005, SC-001, SC-003, DESIGN-REQ-009, DESIGN-REQ-017)
- [X] T006 [P] Add failing test that applying a preview replaces the selected Preset step with editable generated steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, SC-004, DESIGN-REQ-006, DESIGN-REQ-009)
- [X] T007 [P] Add failing test that preview expansion failure or generated-step validation failure leaves the draft unchanged and shows a visible error in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, FR-003, FR-009, SC-002, DESIGN-REQ-017)
- [X] T008 [P] Add failing test that unresolved Preset steps block task submission by default in `frontend/src/entrypoints/task-create.test.tsx` (FR-010, DESIGN-REQ-019)
- [X] T009 [P] Add failing test that step-editor preset preview/apply works without using the separate Task Presets management section in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-011, SC-005, DESIGN-REQ-007)
- [X] T010 Run focused Vitest for `frontend/src/entrypoints/task-create.test.tsx` to confirm new MM-558 tests fail for expected preview/apply gaps

### Integration Tests (write first) ⚠️

- [X] T011 Treat focused Create page Vitest render/submission coverage as the story integration boundary and confirm failures from T010 cover the public UI contract in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation

- [X] T012 Add per-step preset preview state and stale-preview invalidation in `frontend/src/entrypoints/task-create.tsx` (FR-004, FR-005)
- [X] T013 Refactor preset expansion helper in `frontend/src/entrypoints/task-create.tsx` so preview can fetch expansion without mutating the draft and apply can reuse the previewed expansion (FR-004, FR-006, DESIGN-REQ-017)
- [X] T014 Render preview results with generated step titles, Step Types, source/origin text when available, and warnings in `frontend/src/entrypoints/task-create.tsx` (FR-005, FR-008, SC-003, DESIGN-REQ-010)
- [X] T015 Replace the selected temporary Preset step with previewed generated steps on apply in `frontend/src/entrypoints/task-create.tsx` (FR-006, FR-007)
- [X] T016 Block submission of unresolved Preset steps in `frontend/src/entrypoints/task-create.tsx` (FR-010, DESIGN-REQ-019)
- [X] T017 Verify existing Mission Control list, small-text, and notice styles are sufficient for preset preview and warning/error states without changing `frontend/src/styles/mission-control.css` (FR-005, FR-008)
- [X] T018 Story validation: Run focused Vitest for `frontend/src/entrypoints/task-create.test.tsx` and fix failures until MM-558 focused tests pass

**Checkpoint**: The story is functional, covered by focused frontend tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validate without adding hidden scope.

- [X] T019 Run focused Vitest for `frontend/src/entrypoints/task-create.test.tsx`
- [X] T020 Run full `./tools/test_unit.sh` when feasible, or record exact blocker in `specs/278-preview-apply-preset-steps/verification.md`
- [X] T021 Run `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-558 in `specs/278-preview-apply-preset-steps/verification.md`

---

## Dependencies & Execution Order

- Phase 1 and Phase 2 are complete.
- T005-T009 must be written before implementation.
- T010-T011 confirm red-first behavior.
- T012-T017 implement only after red-first confirmation.
- T018 validates focused frontend behavior.
- T019-T021 are final validation.

## Parallel Example

```text
T005, T006, T007, T008, and T009 can be drafted in parallel because they touch the same test file but cover independent test cases; merge them before T010.
```

## Implementation Strategy

1. Add failing frontend tests for preview-before-apply, warnings, no draft mutation, apply replacement, unresolved submit blocking, and step-editor-only preset use.
2. Add preview state and expansion helper reuse in the Create page.
3. Render preview details and apply from current preview.
4. Block unresolved Preset submission.
5. Run focused UI tests, managed unit validation, and MoonSpec verification.
