# Tasks: Masked Conic Beam Geometry and Layers

**Input**: Design documents from `specs/235-masked-conic-beam-geometry-layers/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/masked-conic-beam-geometry.md`, `quickstart.md`

**Tests**: Unit tests and component-level integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around one story: Render Border-Ring Beam Geometry.

**Source Traceability**: Tasks reference FR-001 through FR-011, SC-001 through SC-006, and DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-011.

**Test Commands**:

- Unit tests: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx`
- Final unit verification: `./tools/test_unit.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing frontend component/test structure is the target for this story.

- [X] T001 Verify existing component, test, and stylesheet paths for MM-466 in `frontend/src/components/MaskedConicBorderBeam.tsx`, `frontend/src/components/MaskedConicBorderBeam.test.tsx`, and `frontend/src/styles/mission-control.css`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new shared infrastructure is required; the existing MM-465 component contract is the foundation.

- [X] T002 Confirm no new package, service, database, or external integration dependency is needed for FR-001 through FR-011 in `specs/235-masked-conic-beam-geometry-layers/plan.md`.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Render Border-Ring Beam Geometry

**Summary**: As a UI engineer, I want the MaskedConicBorderBeam surface to render layered conic-gradient beam geometry clipped to the border ring so active execution motion remains on the perimeter and never covers content.

**Independent Test**: Render MaskedConicBorderBeam in active, inactive, glow, trail, and custom geometry states, then inspect DOM attributes and CSS rules to verify the beam and glow are layered, conic-gradient based, masked to the border ring, and excluded from the content area.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-011.

**Test Plan**:

- Unit: CSS contract tests for geometry variables, border-ring mask, conic footprint, glow footprint, trail speed invariant, and MM-466 traceability.
- Integration: Component-level rendering tests for active layered geometry around arbitrary child content, inactive behavior preservation, and decorative layer separation.

### Unit Tests (write first)

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [X] T003 Add failing traceability and root geometry variable tests for MM-466, `--beam-head-arc`, `--beam-tail-arc`, `--beam-inner-inset`, and `--beam-inner-radius` in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-004, FR-005, FR-006, FR-011, SC-002, SC-003, SC-006, DESIGN-REQ-005, DESIGN-REQ-006.
- [X] T004 Add failing CSS contract tests for border-ring mask inset/radius and conic-gradient transparent/tail/head/fade footprint in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-003, FR-004, FR-005, FR-006, FR-007, DESIGN-REQ-005, DESIGN-REQ-006.
- [X] T005 Add failing CSS contract tests for glow footprint, blur, lower opacity, and trail speed invariance in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-008, FR-009, SC-004, DESIGN-REQ-004.

### Integration Tests (write first)

- [X] T006 Add failing component-level integration coverage for active layered geometry around nested text/control content in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-001, FR-002, FR-010, SC-001, SC-005, DESIGN-REQ-004, DESIGN-REQ-011.
- [X] T007 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` to confirm T003-T006 fail for the expected missing MM-466 geometry and traceability evidence.

### Implementation

- [X] T008 Extend `MASKED_CONIC_BORDER_BEAM_TRACEABILITY` in `frontend/src/components/MaskedConicBorderBeam.tsx` to preserve MM-465 and add MM-466 plus DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-011 for FR-011/SC-006.
- [X] T009 Add root CSS variables for beam head arc, tail arc, inner inset, inner radius, and transparent footprint stops in `frontend/src/styles/mission-control.css` for FR-004, FR-005, FR-006, FR-007, SC-002, SC-003.
- [X] T010 Update beam, glow, and trail CSS in `frontend/src/styles/mission-control.css` to consume the geometry variables while preserving border-ring masking, lower-opacity blurred glow, and shared orbit speed for FR-003, FR-007, FR-008, FR-009.
- [X] T011 Update content/layer radius CSS in `frontend/src/styles/mission-control.css` to use the derived inner radius and preserve readable, unmasked content for FR-005, FR-010, DESIGN-REQ-011.

### Story Validation

- [X] T012 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and verify all MM-466 focused unit and component-level integration tests pass.
- [X] T013 Update requirement status evidence in `specs/235-masked-conic-beam-geometry-layers/plan.md` after implementation and focused tests pass.

**Checkpoint**: The story is fully functional, covered by focused unit/component integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T014 Run quickstart validation from `specs/235-masked-conic-beam-geometry-layers/quickstart.md`.
- [X] T015 Run `./tools/test_unit.sh` for final unit-suite verification.
- [X] T016 Run `/speckit.verify` by creating `specs/235-masked-conic-beam-geometry-layers/verification.md` with MM-466, DESIGN-REQ-* coverage, test evidence, and final verdict.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on Story validation.

### Within The Story

- T003-T006 must be written before production implementation.
- T007 must confirm the intended red state before T008-T011.
- T008-T011 may be implemented after red-first confirmation.
- T012 validates focused tests after implementation.
- T013 updates plan evidence only after T012 passes.
- T014-T016 run after story validation.

### Parallel Opportunities

- T003, T004, T005, and T006 all touch the same test file and should be coordinated as one test-authoring batch.
- T008 touches the component file and T009-T011 touch CSS; after T007, T008 can be done independently from the CSS edits.
- T014 and T016 are documentation/evidence tasks but should wait for T012 and T015 evidence.

---

## Implementation Strategy

1. Complete setup/foundation confirmation.
2. Add tests for the missing/partial and implemented-unverified MM-466 requirements.
3. Run the focused UI test and record the red-first failure.
4. Implement the smallest component/CSS changes needed to satisfy the geometry contract.
5. Run the focused UI test until it passes.
6. Update plan evidence, run full unit verification, and produce final `/speckit.verify` evidence.

---

## Notes

- This task list covers one story only.
- Existing MM-465 behavior and traceability must be preserved while adding MM-466 evidence.
- Do not adopt the component on a specific Mission Control page in this story.
- Do not add new public props for head/tail arcs unless tests show CSS variables cannot satisfy the contract.
