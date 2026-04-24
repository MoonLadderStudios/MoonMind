# Tasks: Mission Control Preset Provenance Surfaces

**Input**: Design documents from `specs/200-mission-control-preset-provenance/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Documentation contract checks and source traceability checks are REQUIRED before and after implementation. Write or define checks first, confirm they fail for missing contract language, then update canonical documentation.

**Test Commands**:

- Focused documentation contract check: `rg -n "Preset provenance|Manual|Preset path|unresolved preset includes|Expansion summaries|subtask|sub-plan|separate workflow" docs/UI/MissionControlArchitecture.md`
- Source traceability check: `rg -n "MM-387|DESIGN-REQ-014|DESIGN-REQ-015|DESIGN-REQ-022|DESIGN-REQ-025|DESIGN-REQ-026" specs/200-mission-control-preset-provenance`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, DESIGN-REQ-022: Mission Control defines preset provenance presentation for preview, edit/rerun, list, and detail surfaces.
- FR-002, DESIGN-REQ-015: Provenance is explanatory metadata and not a runtime execution concept.
- FR-003, SC-002: task detail allows Manual, Preset, and Preset path summaries or chips.
- FR-004: flat steps remain the primary execution ordering model.
- FR-005, SC-003, DESIGN-REQ-014: `/tasks/new` can preview composed presets and blocks unresolved includes before runtime submission.
- FR-006, SC-004, DESIGN-REQ-025: expansion summaries remain secondary to canonical execution evidence.
- FR-007, DESIGN-REQ-026: vocabulary avoids subtask, sub-plan, and separate workflow-run labels for preset includes.
- FR-008: canonical docs remain desired-state; volatile planning stays under `local-only handoffs` and `specs/`.
- FR-009, SC-006: MM-387 and original Jira preset brief remain visible in artifacts and verification evidence.

## Phase 1: Setup

- [X] T001 Confirm active MM-387 feature directory and source input in `.specify/feature.json`, `spec.md` (Input), and `specs/200-mission-control-preset-provenance/spec.md` (FR-009, SC-006).
- [X] T002 Confirm `docs/UI/MissionControlArchitecture.md` is the canonical documentation target and `docs/Tasks/PresetComposability.md` is absent in the current checkout in `specs/200-mission-control-preset-provenance/research.md` (FR-008).

## Phase 2: Foundational

- [X] T003 Confirm the existing Mission Control architecture sections for task list, detail, submit integration, artifact evidence, and compatibility vocabulary in `docs/UI/MissionControlArchitecture.md` (FR-001 through FR-007).

## Phase 3: Story - Mission Control Preset Provenance Surfaces

**Summary**: As a Mission Control operator, I want task lists, detail pages, and create/edit flows to explain preset-derived work without implying nested runtime behavior.

**Independent Test**: Review `docs/UI/MissionControlArchitecture.md` and confirm it defines preset provenance presentation for preview, edit/rerun, task list/detail, submit, evidence hierarchy, and vocabulary while preserving flat execution semantics.

**Traceability**: FR-001 through FR-009, SC-001 through SC-006, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-022, DESIGN-REQ-025, DESIGN-REQ-026, MM-387.

### Unit Tests

- [X] T004 Add or confirm the focused documentation contract check command in `specs/200-mission-control-preset-provenance/quickstart.md` (FR-001 through FR-007, SC-001 through SC-005).
- [X] T005 Add or confirm the source traceability check command in `specs/200-mission-control-preset-provenance/quickstart.md` (FR-009, SC-006).

### Integration Tests

- [X] T006 Add or confirm end-to-end review criteria in `specs/200-mission-control-preset-provenance/contracts/mission-control-preset-provenance.md` covering preview, detail, flat step ordering, submit blocking, evidence hierarchy, and vocabulary (FR-001 through FR-007, SC-001 through SC-005).

### Red-First Confirmation

- [X] T007 Run `rg -n "Preset provenance|Manual|Preset path|unresolved preset includes|Expansion summaries|subtask|sub-plan|separate workflow" docs/UI/MissionControlArchitecture.md` and confirm it fails or is incomplete before documentation edits (FR-001 through FR-007, SC-001 through SC-005).

### Implementation

- [X] T008 Update Mission Control task list, detail, and edit/rerun architecture in `docs/UI/MissionControlArchitecture.md` to define preset provenance presentation as explanatory metadata (FR-001, FR-002, DESIGN-REQ-022).
- [X] T009 Update task detail step presentation in `docs/UI/MissionControlArchitecture.md` to allow Manual, Preset, and Preset path summaries or chips while preserving flat step ordering (FR-003, FR-004).
- [X] T010 Update submit integration rules in `docs/UI/MissionControlArchitecture.md` to allow composed preset previews and forbid unresolved preset includes as runtime work (FR-005, SC-003, DESIGN-REQ-014, DESIGN-REQ-015).
- [X] T011 Update artifact/evidence hierarchy and compatibility vocabulary in `docs/UI/MissionControlArchitecture.md` so expansion summaries are secondary evidence and preset includes are not labeled as subtasks, sub-plans, or separate workflow runs (FR-006, FR-007, SC-004, SC-005, DESIGN-REQ-025, DESIGN-REQ-026).

### Story Validation

- [X] T012 Run focused documentation contract and source traceability checks, then fix `docs/UI/MissionControlArchitecture.md` or MoonSpec artifacts as needed (FR-001 through FR-009, SC-001 through SC-006).

## Phase 4: Polish And Verification

- [X] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or record the exact environment blocker in `specs/200-mission-control-preset-provenance/verification.md` (FR-008).
- [X] T014 Run `/moonspec-verify` and record the result in `specs/200-mission-control-preset-provenance/verification.md` (FR-009, SC-006).

## Dependencies & Execution Order

- T001-T003 must complete before story validation.
- T004-T006 define the validation surface before implementation.
- T007 must run before T008-T011.
- T008-T011 all edit `docs/UI/MissionControlArchitecture.md` and should run sequentially.
- T012 validates the story before full unit and final verification tasks.

## Parallel Opportunities

- T004 and T005 can be reviewed independently because they validate different quickstart checks.
- T006 can be reviewed independently from quickstart command checks.

## Notes

- This task list covers exactly one story: MM-387.
- Runtime mode is preserved by treating `docs/UI/MissionControlArchitecture.md` as the source of runtime UI behavior requirements.
