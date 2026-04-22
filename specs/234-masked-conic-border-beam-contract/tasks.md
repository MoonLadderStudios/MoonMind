# Tasks: MaskedConicBorderBeam Border-Only Contract

**Input**: `specs/234-masked-conic-border-beam-contract/spec.md`  
**Plan**: `specs/234-masked-conic-border-beam-contract/plan.md`

**Story Count**: Exactly one story: `Provide Standalone Border Beam Surface`.
**Unit Test Command**: `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx`
**Integration Test Strategy**: Component-level UI integration is covered by rendering the React surface with arbitrary child content and inspecting DOM/CSS contract behavior in Vitest; no service, database, or external runtime integration is required for this presentation-only surface.
**Final Verification**: `/moonspec-verify` against `specs/234-masked-conic-border-beam-contract/spec.md`.

## Unit Tests First

- [X] T001 Add failing component contract tests for default inputs, custom inputs, active layers, inactive behavior, reduced-motion mode, decorative accessibility, and MM-465 traceability in `frontend/src/components/MaskedConicBorderBeam.test.tsx`.
- [X] T002 Add failing CSS contract tests for conic-gradient border-ring masking, reduced-motion media query, and excluded full-card shimmer/spinner/completion/success behavior in `frontend/src/components/MaskedConicBorderBeam.test.tsx`.
- [X] T003 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx` and record the expected red result before production code.

## Integration Tests First

- [X] T004 Add failing component-level integration coverage in `frontend/src/components/MaskedConicBorderBeam.test.tsx` proving arbitrary child content remains readable while active, inactive, custom input, and reduced-motion states preserve the public UI contract.
- [X] T005 Confirm no broader service/database integration fixture is required for this presentation-only component by documenting the integration strategy in `specs/234-masked-conic-border-beam-contract/plan.md`.

## Implementation

- [X] T006 Implement `frontend/src/components/MaskedConicBorderBeam.tsx` with the declared prop contract, deterministic defaults, stable data attributes, and active/inactive layer behavior.
- [X] T007 Add `masked-conic-border-beam` CSS to `frontend/src/styles/mission-control.css` with resting border, conic beam, ring mask, optional glow/trail variants, direction, and reduced-motion handling.
- [X] T008 Ensure the component remains decorative by keeping beam layers `aria-hidden` and leaving status text responsibility to callers.

## Validation

- [X] T009 Run `npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx`.
- [X] T010 Run `./tools/test_unit.sh`.
- [X] T011 Run `/moonspec-verify` by creating `verification.md` with MM-465, DESIGN-REQ-* coverage, test evidence, and final verdict.

## Source Traceability

- T001/T006 cover FR-001, FR-002, FR-006, SC-001, DESIGN-REQ-002.
- T001/T004/T007 cover FR-003, FR-004, FR-005, SC-002, SC-003, DESIGN-REQ-001, DESIGN-REQ-003.
- T001/T002/T004/T007 cover FR-007, SC-004, DESIGN-REQ-010.
- T002/T008 cover FR-008, FR-009, DESIGN-REQ-016.
- T011 covers FR-010 and SC-005.
