# Tasks: Preview and Apply Preset Steps

**Input**: Design documents from `/specs/291-preview-apply-preset-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Existing MM-558/MM-565 red-first coverage established the underlying preview/apply behavior, and active MM-578 tests in `frontend/src/entrypoints/task-create.test.tsx` now preserve story-specific evidence; rerun focused validation and only patch code if evidence fails.

**Organization**: Tasks are grouped by phase around MM-578's single user story.

**Source Traceability**: FR-001..FR-008, SC-001..SC-005, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active MM-578 artifacts and existing Create page boundaries.

- [X] T001 Confirm active MM-578 feature artifacts in `specs/291-preview-apply-preset-steps/spec.md`, `specs/291-preview-apply-preset-steps/plan.md`, `specs/291-preview-apply-preset-steps/research.md`, `specs/291-preview-apply-preset-steps/data-model.md`, `specs/291-preview-apply-preset-steps/contracts/create-page-preset-preview-apply.md`, and `specs/291-preview-apply-preset-steps/quickstart.md`
- [X] T002 Confirm existing Create page implementation and test files in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Reuse existing preset catalog/detail/expand surfaces and generated step mapping.

- [X] T003 Verify existing task-template detail and expand calls in `frontend/src/entrypoints/task-create.tsx` are the authoritative preview/apply source (FR-002, FR-003, DESIGN-REQ-013)
- [X] T004 Verify existing generated step mapping in `frontend/src/entrypoints/task-create.tsx` produces editable executable step state (FR-004, FR-005, DESIGN-REQ-012)

**Checkpoint**: Foundation ready - story verification can begin

---

## Phase 3: Story - Preview and Apply Preset Steps

**Summary**: As a task author, I can select a Preset inside the step editor, configure inputs, preview generated steps, and apply the preset into ordinary executable steps.

**Independent Test**: Render the Create page, choose Step Type `Preset`, select a preset, preview generated Tool/Skill steps and warnings, apply the preview, and verify the Preset placeholder is replaced by editable concrete executable steps.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-004, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019

**Test Plan**:

- Unit: preview state, generated step list/warnings, no mutation before apply, apply replacement, unresolved submit block, preview failure handling, stale preview invalidation.
- Integration: Create page Vitest render/submission coverage acts as the story integration boundary because it exercises UI state and mocked task-template API calls.

### Unit Tests (red-first and active MM-578 evidence)

- [X] T005 Confirm active MM-578 unit coverage for Step Type `Preset` preview generated step titles, Step Types, and expansion warnings without mutating the draft in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, SC-001, SC-003, DESIGN-REQ-012, DESIGN-REQ-013)
- [X] T006 Confirm active MM-578 unit coverage for applying a preview by replacing the selected Preset step with editable generated steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-005, SC-004, DESIGN-REQ-004, DESIGN-REQ-012)
- [X] T007 Confirm active MM-578 unit coverage for preview expansion failure leaving the draft unchanged with a visible error in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, FR-006, FR-008, SC-002, DESIGN-REQ-013)
- [X] T008 Confirm active MM-578 unit coverage for unresolved Preset steps blocking task submission by default in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, DESIGN-REQ-004)
- [X] T009 Confirm active MM-578 unit coverage for step-editor preset preview/apply without using the separate Task Presets management section in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-007, SC-005, DESIGN-REQ-011, DESIGN-REQ-019)
- [X] T010 Confirm active MM-578 stale preset detail/preview invalidation coverage in `frontend/src/entrypoints/task-create.test.tsx` (FR-008, SC-002)

### Integration Tests (active MM-578 evidence)

- [X] T011 Confirm Create page render/submission tests in `frontend/src/entrypoints/task-create.test.tsx` exercise the public authoring boundary: preset preview, apply, generated Tool submission, and unresolved Preset rejection (FR-001, FR-004, FR-006, FR-007, SC-001, SC-004, SC-005)
- [X] T012 Run focused Vitest integration boundary for `frontend/src/entrypoints/task-create.test.tsx` and confirm MM-578 evidence passes

### Implementation

- [X] T013 Verify existing preview state and stale-preview invalidation in `frontend/src/entrypoints/task-create.tsx` satisfy MM-578 without code changes (FR-003, FR-008)
- [X] T014 Verify existing preset expansion helper flow in `frontend/src/entrypoints/task-create.tsx` previews without mutating the draft and applies the current preview (FR-002, FR-003, FR-004, DESIGN-REQ-013)
- [X] T015 Verify existing preview rendering in `frontend/src/entrypoints/task-create.tsx` shows generated step titles, Step Types, source/origin text when available, warnings, and errors (FR-003, FR-008, SC-003)
- [X] T016 Verify existing apply/submission behavior in `frontend/src/entrypoints/task-create.tsx` replaces Preset placeholders with editable executable Tool/Skill steps and blocks unresolved Preset submission (FR-004, FR-005, FR-006, DESIGN-REQ-012)
- [X] T017 Skip contingency patch for MM-578 preview/apply behavior because T012 passed; no production code changes required in `frontend/src/entrypoints/task-create.tsx`

### Story Validation

- [X] T018 Validate the single MM-578 story end-to-end by comparing `specs/291-preview-apply-preset-steps/spec.md`, `specs/291-preview-apply-preset-steps/plan.md`, `frontend/src/entrypoints/task-create.tsx`, and `frontend/src/entrypoints/task-create.test.tsx` (FR-001..FR-008, SC-001..SC-005)

**Checkpoint**: The story is functional, covered by focused frontend tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validate without adding hidden scope.

- [X] T019 Run focused Vitest through `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx`
- [X] T020 Run `./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx`; record the broader full-wrapper Python-suite flake separately in `specs/291-preview-apply-preset-steps/verification.md`
- [X] T021 Run final `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-578 in `specs/291-preview-apply-preset-steps/verification.md`

---

## Dependencies & Execution Order

- Phase 1 and Phase 2 are complete from artifact/code inspection.
- T005-T010 are complete from active MM-578 unit evidence, with MM-558/MM-565 red-first coverage retained as the behavior history.
- T011-T012 cover the Create page integration boundary and must pass before contingency implementation is skipped.
- T013-T016 verify existing implementation; T017 records skipped contingency implementation after verification passes.
- T018 validates the story end-to-end before final verification.
- T019-T021 are final validation.

## Parallel Example

```text
T017 can patch implementation only if T012 exposes a focused preview/apply regression; otherwise it is marked complete as skipped contingency work.
```

## Implementation Strategy

1. Preserve MM-578 source traceability in new Moon Spec artifacts.
2. Verify existing Create page preview/apply tests against the current codebase.
3. Patch only if verification exposes drift from MM-578 requirements.
4. Record final MoonSpec verification for MM-578.
