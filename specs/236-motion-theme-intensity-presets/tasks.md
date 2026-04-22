# Tasks: Motion, Theme, and Intensity Presets

**Input**: Design documents from `specs/236-motion-theme-intensity-presets/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/masked-conic-border-beam-presets.md`, `quickstart.md`

## Validation Commands

- Unit tests: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx`
- Full unit suite: `./tools/test_unit.sh`

## Source Traceability Summary

- MM-467: Tune motion, theme, and intensity presets.
- DESIGN-REQ-007: motion behavior, speed mappings, linear orbit, entry/exit timings.
- DESIGN-REQ-008: color/theme token roles.
- DESIGN-REQ-009: recommended default tuning.
- DESIGN-REQ-012: precision, energized, and dual-phase motion variants.
- DESIGN-REQ-011: preserve border-only rendering and content readability.

## Phase 1: Setup

- [X] T001 Verify existing component, test, and stylesheet paths for MM-467 in `frontend/src/components/MaskedConicBorderBeam.tsx`, `frontend/src/components/MaskedConicBorderBeam.test.tsx`, and `frontend/src/styles/mission-control.css`.

## Phase 2: Foundational

- [X] T002 Confirm no new package, service, database, or external integration dependency is needed for FR-001 through FR-012 in `specs/236-motion-theme-intensity-presets/plan.md`.

## Phase 3: Story - Tune Border Beam Presets

**Summary**: As a product designer or UI engineer, I want MaskedConicBorderBeam to expose predictable motion, theme, intensity, trail, glow, and variant tuning so execution state remains consistent across dark and light MoonMind surfaces.

**Independent Test**: Render MaskedConicBorderBeam with default, speed preset, explicit duration, direction, theme, intensity, trail, glow, and variant states, then inspect DOM attributes and CSS rules to verify default timing, linear orbit, transition timings, token mappings, and traceability for MM-467.

**Traceability IDs**: FR-001 through FR-012; SC-001 through SC-007; DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-011, DESIGN-REQ-012.

### Unit Test Plan

- Component tests cover default props, speed mapping, custom duration pass-through, variant attributes, custom theme style pass-through, and traceability export.
- CSS contract tests cover transition timing tokens, linear orbit, reverse direction, theme/intensity token overrides, variant mappings, and border-only preservation.

### Integration Test Plan

- Component-level integration tests render nested content with tuned theme/intensity/variant settings and verify decorative layers remain separate from content.

### Tests First

- [X] T003 Add failing MM-467 traceability and default tuning tests in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-001, FR-010, FR-012, SC-001, SC-007, DESIGN-REQ-009.
- [X] T004 Add failing speed mapping and explicit duration tests in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-002, FR-003, SC-002, DESIGN-REQ-007.
- [X] T005 Add failing CSS contract tests for transition timings, linear orbit, reverse direction, and speed invariants in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-004, FR-008, SC-003, SC-004, DESIGN-REQ-007.
- [X] T006 Add failing theme, intensity, custom token, and variant tests in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-005, FR-006, FR-007, FR-009, FR-010, SC-005, SC-006, DESIGN-REQ-008, DESIGN-REQ-012.
- [X] T007 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` to confirm T003-T006 fail for expected missing MM-467 evidence.

### Implementation

- [X] T008 Extend `MASKED_CONIC_BORDER_BEAM_TRACEABILITY` and public prop types in `frontend/src/components/MaskedConicBorderBeam.tsx` for MM-467 and variant support.
- [X] T009 Add rendered `data-variant` and companion layer behavior in `frontend/src/components/MaskedConicBorderBeam.tsx` for precision, energized, and dual-phase variants.
- [X] T010 Add transition timing tokens, theme/intensity token refinements, custom-theme pass-through support, and variant CSS mappings in `frontend/src/styles/mission-control.css`.
- [X] T011 Preserve border-only mask, linear orbit, direction reversal, and content readability while applying tuning controls in `frontend/src/styles/mission-control.css`.

### Story Validation

- [X] T012 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and verify all MM-467 focused unit and component-level integration tests pass.
- [X] T013 Update requirement status evidence in `specs/236-motion-theme-intensity-presets/plan.md` after implementation and focused tests pass.

## Final Phase: Polish and Verification

- [X] T014 Run quickstart validation from `specs/236-motion-theme-intensity-presets/quickstart.md`.
- [X] T015 Run `./tools/test_unit.sh` for final unit-test verification.
- [X] T016 Run `/moonspec-verify` by creating `specs/236-motion-theme-intensity-presets/verification.md` with MM-467, DESIGN-REQ-* coverage, test evidence, and final verdict.

## Dependencies and Execution Order

1. T001-T002 establish current paths and dependency assumptions.
2. T003-T007 create and confirm failing tests before implementation.
3. T008-T011 implement the runtime story.
4. T012-T016 validate, update evidence, and verify.

## Parallel Examples

- T003 and T004 can be drafted together because they add independent test cases in the same test file but must be reviewed for ordering conflicts.
- T010 and T011 are both CSS work and should be applied together to avoid partial visual contract changes.

## Implementation Strategy

Write the MM-467 tests first against the existing component. Implement the smallest component and CSS changes needed to satisfy the public preset contract. Keep all behavior inside the existing `MaskedConicBorderBeam` surface, preserve previous MM-465/MM-466 tests, and avoid adopting the effect on unrelated Mission Control cards.
