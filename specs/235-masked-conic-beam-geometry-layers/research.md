# Research: Masked Conic Beam Geometry and Layers

## FR-001 / DESIGN-REQ-004 Layered Visual Geometry

Decision: Reuse the existing `MaskedConicBorderBeam` component and strengthen tests around host content, static border, beam, glow, and content layering.
Evidence: `frontend/src/components/MaskedConicBorderBeam.tsx` renders root, beam layer, optional glow layer, and content wrapper; `frontend/src/styles/mission-control.css` defines border, beam, glow, and content CSS.
Rationale: MM-465 already created the standalone contract. MM-466 should complete the geometry/layer evidence without introducing a duplicate component.
Alternatives considered: Create a new geometry-specific component; rejected because it would fragment the established contract.
Test implications: Unit and component-level integration tests.

## FR-003 / FR-004 / FR-005 / DESIGN-REQ-005 Border Ring Mask

Decision: Add stable CSS variables for the derived inner inset and inner radius, then verify mask composition and radius derivation through CSS contract tests.
Evidence: Existing CSS uses `padding: var(--beam-border-width)` and `mask-composite: exclude`, and content radius uses `calc(var(--beam-border-radius) - var(--beam-border-width))`.
Rationale: The current implementation is close, but MM-466 requires explicit, verifiable geometry for inner inset and optically adjusted radius.
Alternatives considered: Rely only on current padding tests; rejected because the story explicitly requires inner inset and radius evidence.
Test implications: Unit CSS contract tests.

## FR-006 / FR-007 / DESIGN-REQ-006 Beam Footprint

Decision: Expose default beam footprint values as CSS custom properties and assert that the conic gradient uses transparent-majority, tail, head, and fade stops.
Evidence: Existing CSS hardcodes `320deg` and `332deg` stops, which implies a 12deg head but does not expose the requested 12deg/28deg defaults.
Rationale: Named variables make the geometry contract inspectable and tunable without changing the public React API.
Alternatives considered: Add React props for headArc and tailArc; rejected because the current story only requires default geometry and verifiable CSS contract, not a new public prop surface.
Test implications: Unit CSS contract tests.

## FR-008 Glow Footprint

Decision: Keep glow as a separate optional layer derived from the beam footprint with lower opacity, blur, and slight outward expansion.
Evidence: Existing glow CSS uses a conic-gradient, `filter: blur(5px)`, lower opacity, and an inset outside the border.
Rationale: The implementation already follows the design, but tests need to prove the glow remains separate and decorative.
Alternatives considered: Remove glow from this story; rejected because MM-466 includes optional glow acceptance criteria.
Test implications: Unit CSS contract tests.

## FR-009 Trail Speed Invariant

Decision: Verify trail variants change only the layer background/footprint while speed remains owned by `--beam-speed` and the shared orbit animation.
Evidence: Existing trail rules override only `.masked-conic-border-beam__layer` background; speed is configured at the root and consumed by layer/glow animation.
Rationale: The story requires trail behavior without orbital speed changes.
Alternatives considered: Introduce separate trail animations; rejected because they would violate the speed invariant and add unnecessary complexity.
Test implications: Unit CSS contract tests.

## FR-010 Content Preservation

Decision: Add active-state render coverage around nested text/control content and assert content wrapper has no animation or mask behavior.
Evidence: Existing tests assert child content is present and `.masked-conic-border-beam__content` has no animation/mask.
Rationale: MM-466 makes content preservation central, so the evidence should explicitly cite active layered geometry.
Alternatives considered: Use browser screenshot tests; rejected for this slice because CSS/DOM contract tests are sufficient and match existing frontend test style.
Test implications: Component-level integration tests.

## FR-011 / SC-006 Traceability

Decision: Extend the existing traceability constant to include MM-466 and DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-011.
Evidence: Existing `MASKED_CONIC_BORDER_BEAM_TRACEABILITY` only records MM-465 and its requirement IDs.
Rationale: Downstream verification and PR metadata need MM-466 preserved without removing MM-465 evidence.
Alternatives considered: Replace MM-465 traceability; rejected because MM-465 remains a completed source story for the same component.
Test implications: Unit traceability test.

## Test Tooling

Decision: Use existing Vitest + Testing Library + PostCSS tests for focused frontend validation, then run `./tools/test_unit.sh` for final unit verification.
Evidence: `frontend/src/components/MaskedConicBorderBeam.test.tsx` already uses this pattern.
Rationale: No backend service, database, or browser-only interaction is needed for the geometry contract.
Alternatives considered: Add Playwright screenshot checks; rejected because the story can be verified deterministically through DOM/CSS contracts and existing test infrastructure.
Test implications: Focused UI test first, full unit suite before finalizing.
