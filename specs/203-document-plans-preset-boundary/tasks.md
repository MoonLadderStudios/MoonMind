# Tasks: Document Plans Overview Preset Boundary

**Input**: Design documents from `specs/203-document-plans-preset-boundary/`
**Prerequisites**: plan.md, spec.md, research.md, contracts/

**Tests**: Documentation contract checks and source traceability checks are REQUIRED before and after implementation. Write or define checks first, confirm they fail or are incomplete for missing boundary language, then update the plans overview.

**Test Commands**:

- Focused documentation contract check: `rg -n "control plane|PlanDefinition|flattened execution graphs|TaskPresetsSystem|SkillAndPlanContracts" docs/tmp/101-PlansOverview.md`
- No canonical migration checklist check: `! rg -n "MM-389|Document plans overview preset boundary|preset boundary" docs --glob '!docs/tmp/**'`
- Source traceability check: `rg -n "MM-389|DESIGN-REQ-001|DESIGN-REQ-020|DESIGN-REQ-024|DESIGN-REQ-025|DESIGN-REQ-026" specs/203-document-plans-preset-boundary docs/tmp/jira-orchestration-inputs/MM-389-moonspec-orchestration-input.md`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, FR-002, SC-001, DESIGN-REQ-001: concise boundary clarification appears near the tasks, skills, presets, and plans content.
- FR-003, FR-004, SC-002, DESIGN-REQ-020: preset composition belongs to the control plane and is resolved before `PlanDefinition` creation.
- FR-005, SC-003, DESIGN-REQ-024: runtime plans remain flattened execution graphs of concrete nodes and edges.
- FR-006, FR-007, SC-004, SC-005, DESIGN-REQ-025: authoring-time semantics link to `TaskPresetsSystem`; runtime semantics link to `SkillAndPlanContracts`.
- FR-008, SC-006, DESIGN-REQ-026: no additional migration checklist is added to canonical docs.
- FR-009, SC-007: MM-389 and original Jira preset brief remain visible in artifacts and verification evidence.
- Acceptance scenarios 1-5: overview review confirms placement, boundary text, links, and no canonical checklist.
- Acceptance scenario 6: traceability review confirms MM-389 remains present.

## Phase 1: Setup

- [X] T001 Confirm active MM-389 feature directory and source input in `.specify/feature.json`, `docs/tmp/jira-orchestration-inputs/MM-389-moonspec-orchestration-input.md`, and `specs/203-document-plans-preset-boundary/spec.md` (FR-009, SC-007).
- [X] T002 Confirm `docs/tmp/101-PlansOverview.md` is the repository-current plans overview target and `docs/Tasks/PresetComposability.md` is absent in the current checkout in `specs/203-document-plans-preset-boundary/research.md` (FR-001, FR-008).

## Phase 2: Foundational

- [X] T003 Inspect existing preset and plan semantics in `docs/Tasks/TaskPresetsSystem.md`, `docs/Tasks/SkillAndPlanContracts.md`, and `docs/tmp/101-PlansOverview.md` before test authoring (FR-003 through FR-007).

## Phase 3: Story - Plans Overview Preset Boundary

**Summary**: As a documentation reader, I want the plans overview to make the preset authoring/runtime boundary discoverable through concise cross-links.

**Independent Test**: Review the plans overview or repository-current equivalent and confirm it contains one concise boundary clarification near the task, skills, presets, and plans content, with links to authoring-time preset composition semantics and runtime plan semantics.

**Traceability**: FR-001 through FR-009, SC-001 through SC-007, DESIGN-REQ-001, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-026, MM-389.

### Unit Tests

- [X] T004 Add focused documentation contract check command in `specs/203-document-plans-preset-boundary/quickstart.md` (FR-001 through FR-007, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-025).
- [X] T005 Add no-canonical-migration-checklist command in `specs/203-document-plans-preset-boundary/quickstart.md` (FR-008, SC-006, DESIGN-REQ-026).
- [X] T006 Add source traceability check command in `specs/203-document-plans-preset-boundary/quickstart.md` (FR-009, SC-007).

### Integration Tests

- [X] T007 Add end-to-end contract review criteria in `specs/203-document-plans-preset-boundary/contracts/plans-preset-boundary.md` covering placement, control-plane boundary, runtime plan semantics, links, and no migration checklist (FR-001 through FR-008, SC-001 through SC-006, DESIGN-REQ-001, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-026).
- [X] T008 Add final story validation commands in `specs/203-document-plans-preset-boundary/quickstart.md` for full unit tests, hermetic integration tests, and `/moonspec-verify` (FR-008, FR-009, SC-007).

### Red-First Confirmation

- [X] T009 Run `rg -n "control plane|PlanDefinition|flattened execution graphs|TaskPresetsSystem|SkillAndPlanContracts" docs/tmp/101-PlansOverview.md` and confirm it is incomplete before documentation edits (FR-001 through FR-007, SC-001 through SC-005).
- [X] T010 Run source traceability check before artifact completion and confirm only the preserved Jira input exists before generated MoonSpec artifacts (FR-009, SC-007).

### Implementation

- [X] T011 Update `docs/tmp/101-PlansOverview.md` with one concise paragraph near tasks, skills, presets, and plans that states preset composition is control-plane work resolved before `PlanDefinition` creation, runtime plans are flattened execution graphs of concrete nodes and edges, and links to `TaskPresetsSystem` and `SkillAndPlanContracts` (FR-001 through FR-007, SC-001 through SC-005, DESIGN-REQ-001, DESIGN-REQ-020, DESIGN-REQ-024, DESIGN-REQ-025).

### Story Validation

- [X] T012 Run focused documentation contract, no-canonical-migration-checklist, and source traceability checks from `specs/203-document-plans-preset-boundary/quickstart.md`, then fix `docs/tmp/101-PlansOverview.md` or MoonSpec artifacts as needed (FR-001 through FR-009, SC-001 through SC-007).

## Phase 4: Polish And Verification

- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record the exact environment blocker in `specs/203-document-plans-preset-boundary/verification.md` (FR-008).
- [X] T014 Run `./tools/test_integration.sh` when Docker is available or record the exact environment blocker in `specs/203-document-plans-preset-boundary/verification.md` (FR-008).
- [X] T015 Run `/moonspec-verify` and record the result in `specs/203-document-plans-preset-boundary/verification.md` (FR-009, SC-007).

## Dependencies & Execution Order

- T001-T003 must complete before story test authoring.
- T004-T008 define the validation surface before implementation.
- T009-T010 must run before T011.
- T011 edits `docs/tmp/101-PlansOverview.md`.
- T012 validates the story before full unit, integration, and final verification tasks.
- T013-T015 complete final evidence after story validation passes or blockers are recorded.

## Parallel Opportunities

- T004 and T006 can be reviewed independently because they validate different quickstart checks.
- T007 can be reviewed independently from quickstart command checks because it touches `specs/203-document-plans-preset-boundary/contracts/plans-preset-boundary.md`.
- T013 and T014 can run independently after T012 when the environment supports both test suites.

## Implementation Strategy

1. Complete setup and source inspection.
2. Define unit-style documentation checks and integration-style review checks.
3. Run red-first checks and capture missing boundary language before editing.
4. Update only `docs/tmp/101-PlansOverview.md` unless validation discovers artifact drift.
5. Rerun focused checks and traceability checks.
6. Run full unit and hermetic integration suites when available.
7. Run `/moonspec-verify` against the preserved MM-389 Jira preset brief.

## Notes

- This task list covers exactly one story: MM-389.
- The standard MoonSpec prerequisite helper may reject managed branch names; use `.specify/feature.json` as the active feature pointer in this managed run.
