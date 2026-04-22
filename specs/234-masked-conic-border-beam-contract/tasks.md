# Tasks: MaskedConicBorderBeam Border-Only Contract

**Input**: `specs/234-masked-conic-border-beam-contract/spec.md`  
**Plan**: `specs/234-masked-conic-border-beam-contract/plan.md`

## Unit Tests First

- [X] T001 Add failing component contract tests for default inputs, custom inputs, active layers, inactive behavior, reduced-motion mode, decorative accessibility, and MM-465 traceability in `frontend/src/components/MaskedConicBorderBeam.test.tsx`.
- [X] T002 Add failing CSS contract tests for conic-gradient border-ring masking, reduced-motion media query, and excluded full-card shimmer/spinner/completion/success behavior in `frontend/src/components/MaskedConicBorderBeam.test.tsx`.
- [X] T003 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and record the expected red result before production code.

## Implementation

- [X] T004 Implement `frontend/src/components/MaskedConicBorderBeam.tsx` with the declared prop contract, deterministic defaults, stable data attributes, and active/inactive layer behavior.
- [X] T005 Add `masked-conic-border-beam` CSS to `frontend/src/styles/mission-control.css` with resting border, conic beam, ring mask, optional glow/trail variants, direction, and reduced-motion handling.
- [X] T006 Ensure the component remains decorative by keeping beam layers `aria-hidden` and leaving status text responsibility to callers.

## Validation

- [X] T007 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx`.
- [X] T008 Run `./tools/test_unit.sh`.
- [X] T009 Update `tasks.md` checkboxes and create `verification.md` with MM-465, DESIGN-REQ-* coverage, test evidence, and final verdict.

## Source Traceability

- T001/T004 cover FR-001, FR-002, FR-006, SC-001, DESIGN-REQ-002.
- T001/T005 cover FR-003, FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-001, DESIGN-REQ-003.
- T001/T002/T005 cover FR-007, SC-004, DESIGN-REQ-010.
- T002/T006 cover FR-008, FR-009, DESIGN-REQ-016.
- T009 covers FR-010 and SC-005.
