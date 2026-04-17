# Tasks: Document Task Snapshot And Compilation Boundary

**Input**: Design documents from `specs/198-document-task-snapshot-boundary/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Documentation contract checks and source traceability checks are REQUIRED before and after implementation. Write or define checks first, confirm they fail for missing contract language, then update canonical documentation.

**Test Commands**:

- Focused documentation contract check: `rg -n "Preset compilation|authoredPresets|source\\?|include-tree|detachment state|live preset catalog" docs/Tasks/TaskArchitecture.md`
- Source traceability check: `rg -n "MM-385|DESIGN-REQ-015|DESIGN-REQ-017|DESIGN-REQ-018|DESIGN-REQ-019|DESIGN-REQ-025|DESIGN-REQ-026" specs/198-document-task-snapshot-boundary docs/tmp/jira-orchestration-inputs/MM-385-moonspec-orchestration-input.md`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, DESIGN-REQ-017: presets are recursively composable authoring objects resolved in the control plane.
- FR-002, DESIGN-REQ-018: preset compilation includes recursive resolution, tree validation, flattening, and provenance preservation.
- FR-003, DESIGN-REQ-019: task normalization preserves authored preset bindings, flattened provenance, manual and preset-derived order, and resolved payloads.
- FR-004, SC-002: task payload includes optional `authoredPresets` and `steps[].source` semantics.
- FR-005, SC-003, DESIGN-REQ-025: snapshot durability preserves pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- FR-006, SC-004, DESIGN-REQ-015, DESIGN-REQ-026: execution-plane workers do not expand presets or depend on live preset catalog correctness.
- FR-007: submitted tasks remain executable, reconstructible, and auditable after catalog changes.
- FR-008: canonical docs remain desired-state; volatile planning stays under `docs/tmp/` and `specs/`.
- FR-009, SC-005: MM-385 and original Jira preset brief remain visible in artifacts and verification evidence.

## Phase 1: Setup

- [X] T001 Confirm active MM-385 feature directory and source input in `.specify/feature.json`, `docs/tmp/jira-orchestration-inputs/MM-385-moonspec-orchestration-input.md`, and `specs/198-document-task-snapshot-boundary/spec.md` (FR-009, SC-005).
- [X] T002 Confirm `docs/Tasks/TaskArchitecture.md` is the canonical documentation target and `docs/Tasks/PresetComposability.md` is absent in the current checkout in `specs/198-document-task-snapshot-boundary/research.md` (FR-008).

## Phase 2: Foundational

- [X] T003 Confirm the existing task architecture contract sections for control-plane responsibilities, `TaskPayload`, snapshot durability, and execution-plane responsibilities in `docs/Tasks/TaskArchitecture.md` (FR-001 through FR-007).

## Phase 3: Story - Task Snapshot And Compilation Boundary

**Summary**: As a control-plane maintainer, I want task architecture to define preset compilation as a control-plane phase and preserve authored preset provenance in task snapshots so submitted work remains executable and reconstructible without live preset lookup.

**Independent Test**: Review `docs/Tasks/TaskArchitecture.md` and confirm it defines control-plane preset compilation, resolved execution payload boundaries, authored preset metadata, per-step source provenance, snapshot durability, and runtime worker independence from live preset expansion.

**Traceability**: FR-001 through FR-009, SC-001 through SC-005, DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025, DESIGN-REQ-026, MM-385.

### Unit Tests

- [X] T004 Add or confirm the focused documentation contract check command in `specs/198-document-task-snapshot-boundary/quickstart.md` (FR-001 through FR-006, SC-001 through SC-004).
- [X] T005 Add or confirm the source traceability check command in `specs/198-document-task-snapshot-boundary/quickstart.md` (FR-009, SC-005).

### Integration Tests

- [X] T006 Add or confirm end-to-end review criteria in `specs/198-document-task-snapshot-boundary/contracts/task-snapshot-compilation-boundary.md` covering control-plane compilation, payload metadata, snapshot durability, and worker boundary semantics (FR-001 through FR-007, SC-001 through SC-004).

### Red-First Confirmation

- [X] T007 Run `rg -n "Preset compilation|authoredPresets|source\\?|include-tree|detachment state|live preset catalog" docs/Tasks/TaskArchitecture.md` and confirm it fails or is incomplete before documentation edits (FR-001 through FR-006, SC-001 through SC-004).

### Implementation

- [X] T008 Update the system snapshot and control-plane responsibility language in `docs/Tasks/TaskArchitecture.md` to define recursively composable presets and compile-time control-plane resolution (FR-001, FR-002, DESIGN-REQ-017, DESIGN-REQ-018).
- [X] T009 Update the representative task payload contract in `docs/Tasks/TaskArchitecture.md` with optional `authoredPresets` and `steps[].source` metadata and runtime semantics (FR-003, FR-004, DESIGN-REQ-019).
- [X] T010 Update snapshot durability rules in `docs/Tasks/TaskArchitecture.md` to preserve pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order (FR-005, FR-007, DESIGN-REQ-025).
- [X] T011 Update execution-plane boundary and invariants in `docs/Tasks/TaskArchitecture.md` to state workers consume resolved payloads, do not expand presets, and do not depend on live preset catalog correctness (FR-006, DESIGN-REQ-015, DESIGN-REQ-026).

### Story Validation

- [X] T012 Run focused documentation contract and source traceability checks, then fix `docs/Tasks/TaskArchitecture.md` or MoonSpec artifacts as needed (FR-001 through FR-009, SC-001 through SC-005).

## Phase 4: Polish And Verification

- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record the exact environment blocker in `specs/198-document-task-snapshot-boundary/verification.md` (FR-008).
- [X] T014 Run `/moonspec-verify` and record the result in `specs/198-document-task-snapshot-boundary/verification.md` (FR-009, SC-005).

## Dependencies & Execution Order

- T001-T003 must complete before story validation.
- T004-T006 define the validation surface before implementation.
- T007 must run before T008-T011.
- T008-T011 all edit `docs/Tasks/TaskArchitecture.md` and should run sequentially.
- T012 validates the story before full unit and final verification tasks.

## Parallel Opportunities

- T004 and T005 can be reviewed independently because they validate different quickstart checks.
- T006 can be reviewed independently from quickstart command checks.

## Notes

- This task list covers exactly one story: MM-385.
- The standard MoonSpec prerequisite helper rejects the managed branch name `mm-385-76c8ce17`; use `.specify/feature.json` as the active feature pointer in this managed run.
