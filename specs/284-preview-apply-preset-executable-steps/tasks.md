# Tasks: Preview and Apply Preset Steps Into Executable Steps

**Input**: Design documents from `/specs/284-preview-apply-preset-executable-steps/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Existing MM-558 red-first tests are reused as implementation evidence for this MM-565 follow-on; rerun focused validation and only patch code if evidence fails.

**Organization**: Tasks are grouped by phase around MM-565's single user story.

**Source Traceability**: FR-001..FR-011, SC-001..SC-006, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-017.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active MM-565 artifacts and existing Create page boundaries.

- [X] T001 Confirm active MM-565 feature artifacts in `specs/284-preview-apply-preset-executable-steps/spec.md`, `specs/284-preview-apply-preset-executable-steps/plan.md`, `specs/284-preview-apply-preset-executable-steps/research.md`, `specs/284-preview-apply-preset-executable-steps/data-model.md`, `specs/284-preview-apply-preset-executable-steps/contracts/create-page-preset-executable-steps.md`, and `specs/284-preview-apply-preset-executable-steps/quickstart.md`
- [X] T002 Confirm existing Create page implementation and test files in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Reuse existing preset catalog/detail/expand surfaces and generated step mapping.

- [X] T003 Verify existing task-template detail and expand calls in `frontend/src/entrypoints/task-create.tsx` are the authoritative preview/apply source (FR-002, FR-003, FR-004, DESIGN-REQ-017)
- [X] T004 Verify existing generated step mapping in `frontend/src/entrypoints/task-create.tsx` produces editable executable step state (FR-006, FR-007, DESIGN-REQ-011)

**Checkpoint**: Foundation ready - story verification can begin

---

## Phase 3: Story - Preview and Apply Preset Steps Into Executable Steps

**Summary**: As a task author, I can choose a Preset from the step editor, configure its inputs, preview deterministic expansion, and apply it into editable executable Tool and Skill steps.

**Independent Test**: Render the Create page, choose Step Type `Preset`, select a preset, preview the generated Tool/Skill steps and warnings, apply the preview, and verify the Preset placeholder is replaced by editable concrete executable steps.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-017

**Test Plan**:

- Unit: preview state, generated step list/warnings, no mutation before apply, apply replacement, unresolved submit block, preview failure handling, explicit reapply/update messaging.
- Integration: Create page Vitest render/submission coverage acts as the story integration boundary because it exercises UI state and mocked task-template API calls.

### Verification Tests

- [X] T005 Confirm existing tests cover Step Type `Preset` preview generated step titles, Step Types, and expansion warnings without mutating the draft in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-005, SC-001, SC-003, DESIGN-REQ-010, DESIGN-REQ-017)
- [X] T006 Confirm existing tests cover applying a preview by replacing the selected Preset step with editable generated steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, FR-007, SC-004, DESIGN-REQ-006, DESIGN-REQ-010)
- [X] T007 Confirm existing tests cover preview expansion failure leaving the draft unchanged with a visible error in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, FR-003, FR-008, SC-002, DESIGN-REQ-017)
- [X] T008 Confirm existing tests cover unresolved Preset steps blocking task submission by default in `frontend/src/entrypoints/task-create.test.tsx` (FR-009, DESIGN-REQ-017)
- [X] T009 Confirm existing tests cover step-editor preset preview/apply without using the separate Task Presets management section in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-010, SC-005, DESIGN-REQ-007)
- [X] T010 Run focused Vitest for `frontend/src/entrypoints/task-create.test.tsx` to confirm MM-565 evidence still passes

### Contingency Implementation

- [ ] T011 If T010 fails for MM-565 preview/apply behavior, patch `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx` only for the failing requirement
- [ ] T012 If explicit newer-version preview coverage is insufficient, add or update focused tests in `frontend/src/entrypoints/task-create.test.tsx` and patch `frontend/src/entrypoints/task-create.tsx` only if the test exposes a product gap (FR-011, SC-006)

**Checkpoint**: The story is functional, covered by focused frontend tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validate without adding hidden scope.

- [X] T013 Run focused Vitest for `frontend/src/entrypoints/task-create.test.tsx`
- [X] T014 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` when feasible, or record exact blocker in `specs/284-preview-apply-preset-executable-steps/verification.md`
- [X] T015 Run `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-565 in `specs/284-preview-apply-preset-executable-steps/verification.md`

---

## Dependencies & Execution Order

- Phase 1 and Phase 2 are complete from artifact/code inspection.
- T005-T009 are complete from existing MM-558 evidence inspection.
- T010 must run before deciding whether T011 or T012 is needed.
- T013-T015 are final validation.

## Parallel Example

```text
T011 and T012 can be handled independently only if focused verification exposes both a preview/apply regression and a separate explicit update-preview gap.
```

## Implementation Strategy

1. Preserve MM-565 source traceability in new Moon Spec artifacts.
2. Verify existing Create page preview/apply tests against the current codebase.
3. Patch only if verification exposes drift from MM-565 requirements.
4. Record final MoonSpec verification for MM-565.
