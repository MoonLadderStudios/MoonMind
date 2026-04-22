# Tasks: Reduced Motion, Accessibility, and Performance Guardrails

**Input**: Design documents from `specs/237-reduced-motion-accessibility-performance/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/masked-conic-border-beam-guardrails.md`, `quickstart.md`

## Validation Commands

- Unit tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/components/MaskedConicBorderBeam.test.tsx`
- Full unit suite: `./tools/test_unit.sh`

## Source Traceability Summary

- MM-468: Support reduced motion, accessibility, and performance guardrails.
- DESIGN-REQ-013: auto and minimal reduced-motion behavior.
- DESIGN-REQ-014: accessible secondary status cue and calm UX constraints.
- DESIGN-REQ-015: transform-based animation and glow/performance degradation.
- DESIGN-REQ-016: static meaningful reduced-motion state and non-goal preservation.

## Phase 1: Setup

- [X] T001 Verify existing component, test, and stylesheet paths for MM-468 in `frontend/src/components/MaskedConicBorderBeam.tsx`, `frontend/src/components/MaskedConicBorderBeam.test.tsx`, and `frontend/src/styles/mission-control.css`.

## Phase 2: Foundational

- [X] T002 Confirm no new package, service, database, or external integration dependency is needed for FR-001 through FR-011 in `specs/237-reduced-motion-accessibility-performance/plan.md`.

## Phase 3: Story - Guard Border Beam Motion and Accessibility

**Summary**: As an operator and end user, I want MaskedConicBorderBeam to respect reduced-motion preferences, expose an accessible execution cue, and avoid expensive animation behavior so execution state remains visible without harming usability or dense-list performance.

**Independent Test**: Render MaskedConicBorderBeam with auto and minimal reduced-motion modes, inspect accessible output and CSS rules, and verify reduced-motion, low-power, non-goal, and performance guardrails preserve a meaningful active state without orbital motion, rapid pulse, content masking, or expensive glow.

**Traceability IDs**: FR-001 through FR-011; SC-001 through SC-007; DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016.

### Unit Test Plan

- Component tests cover default status label, custom status label, inactive label omission, label suppression, decorative `aria-hidden` layers, and MM-468 traceability.
- CSS contract tests cover auto/minimal reduced-motion behavior, glow/companion degradation, transform-based animation, layout-animation exclusions, warning-pulse exclusions, modest tokens, and non-goal preservation.

### Integration Test Plan

- Component-level integration tests render active content with reduced-motion modes and verify the primary content remains readable while active cues degrade to static border-only forms.

### Tests First

- [X] T003 Add failing MM-468 traceability and accessible status label tests in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-004, FR-005, FR-011, SC-001, SC-007, DESIGN-REQ-014.
- [X] T004 Add failing CSS contract tests for auto and minimal reduced-motion guardrails in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-001, FR-002, FR-003, FR-008, SC-002, SC-003, DESIGN-REQ-013, DESIGN-REQ-015.
- [X] T005 Add failing CSS contract tests for transform-based animation, layout-triggering animation exclusions, modest luminance/glow, and warning/non-goal exclusions in `frontend/src/components/MaskedConicBorderBeam.test.tsx` covering FR-006, FR-007, FR-009, FR-010, SC-004, SC-005, SC-006, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016.
- [X] T006 Run focused Vitest validation to confirm T003-T005 fail for expected missing MM-468 evidence.

### Implementation

- [X] T007 Extend `MASKED_CONIC_BORDER_BEAM_TRACEABILITY`, public prop types, and active status label rendering in `frontend/src/components/MaskedConicBorderBeam.tsx` for MM-468.
- [X] T008 Update reduced-motion CSS in `frontend/src/styles/mission-control.css` so auto preference keeps a static primary segment while disabling glow/companion first, and minimal mode uses a brighter static border ring only.
- [X] T009 Preserve transform-based orbit animation, modest glow tokens, border-only masks, and non-goal guardrails in `frontend/src/styles/mission-control.css`.

### Story Validation

- [X] T010 Run focused Vitest validation and verify all MM-468 focused unit and component-level integration tests pass.
- [X] T011 Update requirement status evidence in `specs/237-reduced-motion-accessibility-performance/plan.md` after implementation and focused tests pass.

## Final Phase: Polish and Verification

- [X] T012 Run quickstart validation from `specs/237-reduced-motion-accessibility-performance/quickstart.md`.
- [X] T013 Run `./tools/test_unit.sh` for final unit-test verification.
- [X] T014 Run `/moonspec-verify` by creating `specs/237-reduced-motion-accessibility-performance/verification.md` with MM-468, DESIGN-REQ-* coverage, test evidence, and final verdict.

## Dependencies and Execution Order

1. T001-T002 establish current paths and dependency assumptions.
2. T003-T006 create and confirm failing tests before implementation.
3. T007-T009 implement the runtime story.
4. T010-T014 validate, update evidence, and verify.

## Parallel Examples

- T003 and T004 can be drafted together because they add independent assertions in the same test file but must be reviewed for ordering conflicts.
- T008 and T009 are both CSS work and should be applied together to avoid partial reduced-motion contract changes.

## Implementation Strategy

Write the MM-468 tests first against the existing component. Implement the smallest component and CSS changes needed to satisfy the public guardrail contract. Keep all behavior inside the existing `MaskedConicBorderBeam` surface, preserve previous MM-465/MM-466/MM-467 tests, and avoid adopting the effect on unrelated Mission Control cards.
