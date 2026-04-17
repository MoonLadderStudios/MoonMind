# Tasks: Document Flattened Plan Execution Contract

**Input**: Design documents from `specs/199-document-flattened-plan-contract/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Documentation contract checks and source traceability checks are REQUIRED before and after implementation. Write or define checks first, confirm they fail or are incomplete for missing contract language, then update canonical documentation.

**Test Commands**:

- Focused documentation contract check: `rg -n "authoring concern|flattened execution contract|unresolved preset include|binding_id|include_path|blueprint_step_slug|detached|provenance" docs/Tasks/SkillAndPlanContracts.md`
- Validation rule check: `rg -n "absent provenance|invalid claimed preset provenance|unresolved preset include|nested preset semantics|never executable logic" docs/Tasks/SkillAndPlanContracts.md`
- Source traceability check: `rg -n "MM-386|DESIGN-REQ-001|DESIGN-REQ-019|DESIGN-REQ-020|DESIGN-REQ-021|DESIGN-REQ-025|DESIGN-REQ-026" specs/199-document-flattened-plan-contract docs/tmp/jira-orchestration-inputs/MM-386-moonspec-orchestration-input.md`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, FR-002, SC-001, DESIGN-REQ-020: preset composition is an authoring concern and stored plans are flattened execution contracts after expansion.
- FR-003, FR-006, SC-002, DESIGN-REQ-021: stored plan artifacts contain executable plan nodes only and unresolved include objects are invalid.
- FR-004, SC-003, DESIGN-REQ-001: plan node examples include optional `binding_id`, `include_path`, `blueprint_step_slug`, and `detached` source provenance.
- FR-005, FR-007, SC-004, DESIGN-REQ-025: validation allows absent provenance and rejects structurally invalid claimed preset provenance.
- FR-008, FR-010, SC-005, DESIGN-REQ-019: manual authoring, preset expansion, and other plan-producing tools all produce the same flattened graph.
- FR-009, FR-010, SC-006, DESIGN-REQ-026: runtime has no nested preset semantics and provenance is never executable logic.
- FR-011: canonical docs remain desired-state; volatile planning stays under `docs/tmp/` and `specs/`.
- FR-012, SC-007: MM-386 and original Jira preset brief remain visible in artifacts and verification evidence.
- Acceptance scenarios 1-6: contract review confirms flat stored plan shape, validation behavior, provenance semantics, authoring-origin-neutral DAG semantics, and runtime executor behavior.
- Acceptance scenario 7: traceability review confirms MM-386 remains present.

## Phase 1: Setup

- [X] T001 Confirm active MM-386 feature directory and source input in `.specify/feature.json`, `docs/tmp/jira-orchestration-inputs/MM-386-moonspec-orchestration-input.md`, and `specs/199-document-flattened-plan-contract/spec.md` (FR-012, SC-007).
- [X] T002 Confirm `docs/Tasks/SkillAndPlanContracts.md` is the canonical documentation target and `docs/Tasks/PresetComposability.md` is absent in the current checkout in `specs/199-document-flattened-plan-contract/research.md` (FR-011).
- [X] T003 Confirm the managed branch limitation for `.specify/scripts/bash/check-prerequisites.sh --json` is recorded in `specs/199-document-flattened-plan-contract/plan.md` (FR-011).

## Phase 2: Foundational

- [X] T004 Inspect existing plan schema, dependency semantics, validation rules, and execution semantics in `docs/Tasks/SkillAndPlanContracts.md` before test authoring (FR-001 through FR-010).

## Phase 3: Story - Flattened Plan Execution Contract

**Summary**: As a runtime contract owner, I want plan contracts to reject unresolved preset includes and treat source provenance as optional metadata so every plan executor receives the same flat graph contract regardless of how the plan was authored.

**Independent Test**: Review `docs/Tasks/SkillAndPlanContracts.md` and confirm it defines flattened plan artifacts, invalid unresolved include entries, optional source provenance, provenance validation, authoring-origin-neutral DAG semantics, and runtime invariants that keep provenance out of executable logic.

**Traceability**: FR-001 through FR-012, SC-001 through SC-007, DESIGN-REQ-001, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-025, DESIGN-REQ-026, MM-386.

### Unit Tests

- [X] T005 Add or confirm the focused documentation contract check command in `specs/199-document-flattened-plan-contract/quickstart.md` (FR-001 through FR-004, FR-008 through FR-010, SC-001 through SC-006, DESIGN-REQ-001, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-026).
- [X] T006 Add or confirm the validation rule check command in `specs/199-document-flattened-plan-contract/quickstart.md` (FR-005 through FR-007, FR-009, SC-002, SC-004, SC-006, DESIGN-REQ-021, DESIGN-REQ-025, DESIGN-REQ-026).
- [X] T007 Add or confirm the source traceability check command in `specs/199-document-flattened-plan-contract/quickstart.md` (FR-012, SC-007).

### Integration Tests

- [X] T008 Add or confirm end-to-end contract review criteria in `specs/199-document-flattened-plan-contract/contracts/flattened-plan-execution-contract.md` covering preset expansion boundary, stored plan artifact shape, provenance metadata, validation behavior, DAG semantics, and executor behavior (FR-001 through FR-010, SC-001 through SC-006, DESIGN-REQ-001, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-025, DESIGN-REQ-026).
- [X] T009 Add or confirm final story validation commands in `specs/199-document-flattened-plan-contract/quickstart.md` for full unit tests, hermetic integration tests, and `/moonspec-verify` (FR-011, FR-012, SC-007).

### Red-First Confirmation

- [X] T010 Run `rg -n "authoring concern|flattened execution contract|unresolved preset include|binding_id|include_path|blueprint_step_slug|detached|provenance" docs/Tasks/SkillAndPlanContracts.md` and confirm it fails or is incomplete before documentation edits (FR-001 through FR-004, FR-008 through FR-010, SC-001 through SC-006).
- [X] T011 Run `rg -n "absent provenance|invalid claimed preset provenance|unresolved preset include|nested preset semantics|never executable logic" docs/Tasks/SkillAndPlanContracts.md` and confirm it fails or is incomplete before documentation edits (FR-005 through FR-007, FR-009, SC-002, SC-004, SC-006).

### Implementation

- [X] T012 Update the plan definition and plan schema narrative in `docs/Tasks/SkillAndPlanContracts.md` to state preset composition is an authoring concern and stored plans are flattened execution contracts after expansion (FR-001, FR-002, SC-001, DESIGN-REQ-020).
- [X] T013 Update PlanDefinition production rules in `docs/Tasks/SkillAndPlanContracts.md` so executable plan nodes are the only valid stored plan nodes and unresolved include objects are invalid stored plan artifact content (FR-003, FR-006, SC-002, DESIGN-REQ-021).
- [X] T014 Update plan node examples in `docs/Tasks/SkillAndPlanContracts.md` with optional source provenance metadata containing `binding_id`, `include_path`, `blueprint_step_slug`, and `detached` (FR-004, FR-005, SC-003, DESIGN-REQ-001).
- [X] T015 Update validation rules in `docs/Tasks/SkillAndPlanContracts.md` to allow absent provenance and reject structurally invalid claimed preset provenance (FR-005, FR-007, SC-004, DESIGN-REQ-025).
- [X] T016 Update DAG semantics and execution invariants in `docs/Tasks/SkillAndPlanContracts.md` to state all plan producers create the same flattened graph, nested preset semantics do not exist at runtime, and provenance is never executable logic (FR-008 through FR-010, SC-005, SC-006, DESIGN-REQ-019, DESIGN-REQ-026).

### Story Validation

- [X] T017 Run focused documentation contract, validation rule, and source traceability checks from `specs/199-document-flattened-plan-contract/quickstart.md`, then fix `docs/Tasks/SkillAndPlanContracts.md` or MoonSpec artifacts as needed (FR-001 through FR-012, SC-001 through SC-007).

## Phase 4: Polish And Verification

- [X] T018 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record the exact environment blocker in `specs/199-document-flattened-plan-contract/verification.md` (FR-011).
- [X] T019 Run `./tools/test_integration.sh` when Docker is available or record the exact environment blocker in `specs/199-document-flattened-plan-contract/verification.md` (FR-011).
- [X] T020 Run `/moonspec-verify` and record the result in `specs/199-document-flattened-plan-contract/verification.md` (FR-012, SC-007).

## Dependencies & Execution Order

- T001-T004 must complete before story test authoring.
- T005-T009 define the validation surface before implementation.
- T010-T011 must run before T012-T016.
- T012-T016 all edit `docs/Tasks/SkillAndPlanContracts.md` and should run sequentially.
- T017 validates the story before full unit, integration, and final verification tasks.
- T018-T020 complete final evidence after story validation passes or blockers are recorded.

## Parallel Opportunities

- T005 and T007 can be reviewed independently because they validate different quickstart checks.
- T008 can be reviewed independently from quickstart command checks because it touches `specs/199-document-flattened-plan-contract/contracts/flattened-plan-execution-contract.md`.
- T018 and T019 can be run independently after T017 when the environment supports both test suites.

## Implementation Strategy

1. Complete setup and foundational inspection.
2. Define unit-style documentation contract checks and integration-style end-to-end review checks.
3. Run red-first checks and capture missing contract language before editing.
4. Update only `docs/Tasks/SkillAndPlanContracts.md` unless validation discovers artifact drift.
5. Rerun focused checks and traceability checks.
6. Run full unit and hermetic integration suites when available.
7. Run `/moonspec-verify` against the preserved MM-386 Jira preset brief.

## Notes

- This task list covers exactly one story: MM-386.
- The standard MoonSpec prerequisite helper rejects the managed branch name `mm-386-8a30061f`; use `.specify/feature.json` as the active feature pointer in this managed run.
