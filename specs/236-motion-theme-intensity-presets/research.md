# Research: Motion, Theme, and Intensity Presets

## FR-001 / DESIGN-REQ-009 Defaults

Decision: Treat default tuning as implemented but unverified for MM-467.
Evidence: `MaskedConicBorderBeam.tsx` defaults to active, 16px radius, 1.5px border, medium speed, clockwise direction, normal intensity, neutral theme, soft trail, low glow, and reducedMotion auto.
Rationale: Existing tests verify several defaults, but they predate MM-467 traceability and do not cover the full preset brief.
Alternatives considered: Mark implemented_verified from MM-465/MM-466 tests; rejected because transition, theme, and variant requirements are not fully covered.
Test implications: Unit and component-level integration tests.

## FR-002 / FR-003 Speed Presets

Decision: Preserve the current `cssSpeed()` behavior and add matrix coverage for named and explicit duration inputs.
Evidence: `cssSpeed()` maps slow to 4.8s, medium to 3.6s, fast to 2.8s, numeric values to seconds, and string values unchanged.
Rationale: This already matches the design source and needs focused evidence.
Alternatives considered: Move mapping to CSS classes; rejected because component-level style variables make explicit durations straightforward and testable.
Test implications: Unit tests.

## FR-004 Direction

Decision: Keep direction as a data attribute and CSS reverse animation for counterclockwise.
Evidence: `data-direction="counterclockwise"` CSS sets `animation-direction: reverse` for beam and glow layers.
Rationale: Direction is a presentation concern and should not alter speed or footprint variables.
Alternatives considered: Separate keyframes per direction; rejected as unnecessary duplication.
Test implications: CSS contract tests.

## FR-005 / FR-006 / FR-007 Theme and Intensity Tokens

Decision: Keep theme and intensity as data attributes backed by CSS custom properties, and document custom theme as caller-provided CSS variable overrides.
Evidence: root CSS defines border, head, tail, glow, beam opacity, and glow opacity; brand/success and subtle/vivid override token subsets.
Rationale: Token mappings support dark/light surfaces while preserving local override capability.
Alternatives considered: Hardcode theme values in React; rejected because CSS tokens are easier to override per surface.
Test implications: CSS token tests and component custom-style pass-through tests.

## FR-008 Entry and Exit Transitions

Decision: Add CSS custom properties for beam enter/exit durations and apply opacity/visibility transitions to beam and glow layers.
Evidence: The current CSS has orbit animation but no explicit transition contract.
Rationale: The design requires 160-240ms activation and 120-180ms deactivation timing ranges while keeping orbit linear.
Alternatives considered: Animate mount/unmount in React; rejected because CSS contract is simpler and avoids state management.
Test implications: CSS contract tests.

## FR-009 / FR-010 Motion Variants

Decision: Add a `variant` prop with `precision`, `energized`, and `dualPhase` values, defaulting to `precision`.
Evidence: No current variant prop exists; trail and glow props cover part of Variant A but not named variant outcomes.
Rationale: Named variants make the design source explicit without changing the established active-state semantics.
Alternatives considered: Treat `trail` as the only variant control; rejected because the brief requires precision glint, energized beam, and dual-phase orbit variants as configurable outcomes or documented mappings.
Test implications: Component and CSS contract tests.

## FR-011 Border-Only Preservation

Decision: Reuse MM-466 geometry and add MM-467 tests that variant/theme/intensity tuning does not remove mask/content separation.
Evidence: Existing tests verify decorative layers, masks, content wrapper, and trail speed invariance.
Rationale: Tuning controls must be proven not to regress the border-only contract.
Alternatives considered: Screenshot visual tests; rejected for this focused unit-level story.
Test implications: Component-level integration tests.

## FR-012 Traceability

Decision: Add MM-467 and DESIGN-REQ-007/008/009/012 to the exported traceability object and verification artifact.
Evidence: Current traceability includes MM-465, MM-466, and earlier design IDs only.
Rationale: The Jira key must be preserved for downstream artifacts and PR metadata.
Alternatives considered: Trace only in specs; rejected because runtime contract exposes prior traceability.
Test implications: Unit test.

