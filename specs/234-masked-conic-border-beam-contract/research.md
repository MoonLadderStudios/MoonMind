# Research: MaskedConicBorderBeam Border-Only Contract

## FR-001 / DESIGN-REQ-001

Decision: Add a reusable frontend component in `frontend/src/components/MaskedConicBorderBeam.tsx`.
Evidence: No existing component or border-beam implementation was found by targeted search for `BorderBeam`, `Conic`, or beam-specific terms.
Rationale: A standalone wrapper satisfies the Jira story without coupling behavior to one Mission Control card.
Alternatives considered: Add styles directly to task cards; rejected because MM-465 asks for a standalone contract.
Test implications: Unit tests render arbitrary child content inside the wrapper.

## FR-002 / DESIGN-REQ-002

Decision: Model all declared inputs as typed props with deterministic defaults.
Evidence: Existing `executionStatusPillClasses` handles status coloring only and does not expose geometry or beam controls.
Rationale: Props are the stable contract downstream surfaces can tune.
Alternatives considered: Use CSS classes only; rejected because the Jira brief requires declared inputs.
Test implications: Unit tests assert default data attributes and CSS variables.

## FR-003, FR-004 / DESIGN-REQ-003

Decision: Render beam and glow layers only when active and constrain them with CSS masking to a border ring.
Evidence: `docs/UI/EffectBorderBeam.md` defines the clean mental model as a conic-gradient highlight masked down to the border ring.
Rationale: DOM layers make inactive behavior and accessibility boundaries easy to test.
Alternatives considered: Pseudo-elements only; rejected because test coverage can more directly inspect layer presence when layers are rendered.
Test implications: CSS contract test checks conic-gradient and mask-composite/exclude style.

## FR-007 / DESIGN-REQ-010

Decision: Support explicit `minimal` reduced motion with no animation and add a `prefers-reduced-motion` CSS media query for `auto`.
Evidence: Source design requires static meaningful active state and no rapid pulse.
Rationale: Combining prop state with media query covers caller-controlled and user-preference paths.
Alternatives considered: JavaScript media-query handling; rejected as unnecessary for a decorative effect.
Test implications: Unit tests assert minimal mode state; CSS test asserts media query exists.

## FR-008 / DESIGN-REQ-016

Decision: Add tests that inspect both DOM and CSS for excluded full-card shimmer, background-fill animation, spinner replacement, completion/success classes, and content masking.
Evidence: Source non-goals explicitly exclude those behaviors.
Rationale: Regression tests make the negative contract executable.
Alternatives considered: Document-only warning; rejected because runtime mode requires executable evidence.
Test implications: Component and CSS tests include non-goal assertions.
