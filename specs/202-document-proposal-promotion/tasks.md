# Tasks: Proposal Promotion Preset Provenance

**Input**: Design documents from `specs/202-document-proposal-promotion/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Documentation contract checks and source traceability checks are REQUIRED before and after implementation. Write or define checks first, confirm they fail for missing contract language, then update canonical documentation.

**Test Commands**:

- Focused documentation contract check: `rg -n "preset-derived metadata|authoredPresets|live preset catalog|live re-expansion|refresh-latest|flattened-only|fabricate.*binding|preset provenance" docs/Tasks/TaskProposalSystem.md`
- Source traceability check: `rg -n "MM-388|DESIGN-REQ-015|DESIGN-REQ-019|DESIGN-REQ-023|DESIGN-REQ-025|DESIGN-REQ-026" specs/202-document-proposal-promotion docs/tmp/jira-orchestration-inputs/MM-388-moonspec-orchestration-input.md`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, SC-001, DESIGN-REQ-023, DESIGN-REQ-025: preset-derived proposal metadata is advisory UX/reconstruction metadata, not a runtime dependency.
- FR-002, FR-005, SC-002, DESIGN-REQ-015: promotion validates and submits the reviewed flat payload without live preset catalog lookup or live re-expansion by default.
- FR-003, SC-003, DESIGN-REQ-019: proposal payload examples may include optional `task.authoredPresets` and per-step `source` provenance.
- FR-004, SC-004: promotion preserves authored preset metadata and per-step provenance by default.
- FR-006: refresh-latest preset behavior is explicit and not default.
- FR-007, SC-005: generators preserve reliable provenance and do not fabricate bindings.
- FR-008, DESIGN-REQ-026: detail and observability distinguish manual, preset-derived with preserved binding metadata, and preset-derived flattened-only work.
- FR-009: canonical docs remain desired-state; volatile planning stays under `docs/tmp/` and `specs/`.
- FR-010, SC-007: MM-388 and original Jira preset brief remain visible in artifacts and verification evidence.

## Phase 1: Setup

- [X] T001 Confirm active MM-388 feature directory and source input in `.specify/feature.json`, `docs/tmp/jira-orchestration-inputs/MM-388-moonspec-orchestration-input.md`, and `specs/202-document-proposal-promotion/spec.md` (FR-010, SC-007).
- [X] T002 Confirm `docs/Tasks/TaskProposalSystem.md` is the canonical documentation target and `docs/Tasks/PresetComposability.md` is absent in the current checkout in `specs/202-document-proposal-promotion/research.md` (FR-009).

## Phase 2: Foundational

- [X] T003 Confirm existing Task Proposal System sections for invariants, generation, payload rules, promotion, and observability in `docs/Tasks/TaskProposalSystem.md` (FR-001 through FR-008).

## Phase 3: Story - Proposal Promotion Preset Provenance

**Summary**: As a proposal reviewer, I want task proposals to preserve reliable preset metadata when available while promoting the reviewed flat task payload without live re-expansion drift.

**Independent Test**: Review `docs/Tasks/TaskProposalSystem.md` and confirm it defines advisory preset provenance semantics, optional authored preset and per-step source fields, default flat-payload promotion without live re-expansion, generator non-fabrication rules, and proposal detail/observability provenance states.

**Traceability**: FR-001 through FR-010, SC-001 through SC-007, DESIGN-REQ-015, DESIGN-REQ-019, DESIGN-REQ-023, DESIGN-REQ-025, DESIGN-REQ-026, MM-388.

### Unit Tests

- [X] T004 Add the focused documentation contract check command in `specs/202-document-proposal-promotion/quickstart.md` (FR-001 through FR-008, SC-001 through SC-006).
- [X] T005 Add the source traceability check command in `specs/202-document-proposal-promotion/quickstart.md` (FR-010, SC-007).

### Integration Tests

- [X] T006 Add end-to-end review criteria in `specs/202-document-proposal-promotion/contracts/proposal-promotion-preset-provenance.md` covering payload examples, promotion preservation, generator guidance, refresh-latest explicitness, and detail/observability states (FR-001 through FR-008, SC-001 through SC-006).

### Red-First Confirmation

- [X] T007 Run `rg -n "preset-derived metadata|authoredPresets|live preset catalog|live re-expansion|refresh-latest|flattened-only|fabricate.*binding|preset provenance" docs/Tasks/TaskProposalSystem.md` and confirm it fails before documentation edits (FR-001 through FR-008, SC-001 through SC-006).

### Implementation

- [X] T008 Update Task Proposal System invariants in `docs/Tasks/TaskProposalSystem.md` to state preset-derived metadata is advisory UX/reconstruction metadata and not a runtime dependency (FR-001, SC-001, DESIGN-REQ-023, DESIGN-REQ-025).
- [X] T009 Update proposal generation guidance in `docs/Tasks/TaskProposalSystem.md` to preserve reliable parent-run preset provenance and forbid fabricated authored preset bindings (FR-007, SC-005).
- [X] T010 Update canonical proposal payload example and rules in `docs/Tasks/TaskProposalSystem.md` to include optional `task.authoredPresets` and per-step `source` provenance alongside execution-ready flat steps (FR-003, SC-003, DESIGN-REQ-019).
- [X] T011 Update promotion flow in `docs/Tasks/TaskProposalSystem.md` to preserve authored preset metadata and per-step provenance by default while validating the flat task payload (FR-002, FR-004).
- [X] T012 Update promotion behavior in `docs/Tasks/TaskProposalSystem.md` to forbid default live preset catalog lookup and live re-expansion, and document any refresh-latest workflow as explicit (FR-002, FR-005, FR-006, DESIGN-REQ-015).
- [X] T013 Update proposal detail and observability guidance in `docs/Tasks/TaskProposalSystem.md` to distinguish manual, preset-derived with preserved binding metadata, and preset-derived flattened-only work (FR-008, DESIGN-REQ-026).

### Story Validation

- [X] T014 Run focused documentation contract and source traceability checks, then fix `docs/Tasks/TaskProposalSystem.md` or MoonSpec artifacts as needed (FR-001 through FR-010, SC-001 through SC-007).

## Phase 4: Polish And Verification

- [X] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record the exact environment blocker in `specs/202-document-proposal-promotion/verification.md` (FR-009).
- [X] T016 Run `/moonspec-verify` and record the result in `specs/202-document-proposal-promotion/verification.md` (FR-010, SC-007).

## Dependencies & Execution Order

- T001-T003 must complete before story validation.
- T004-T006 define the validation surface before implementation.
- T007 must run before T008-T013.
- T008-T013 all edit `docs/Tasks/TaskProposalSystem.md` and should run sequentially.
- T014 validates the story before full unit and final verification tasks.

## Parallel Opportunities

- T004 and T005 can be reviewed independently because they validate different quickstart checks.
- T006 can be reviewed independently from quickstart command checks.

## Notes

- This task list covers exactly one story: MM-388.
- Runtime mode is preserved by treating `docs/Tasks/TaskProposalSystem.md` as the source of runtime proposal behavior requirements.
