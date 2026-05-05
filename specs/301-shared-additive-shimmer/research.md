# Research: Shared Additive Shimmer Masks

## FR-001 / DESIGN-REQ-001

Decision: Mark implemented_verified; the active shimmer uses one shared moving light field exposed through fill, border, and text masks.
Evidence: `frontend/src/styles/mission-control.css` defines `--mm-executing-moving-light-gradient`; the active status selectors apply that gradient to `::before`, `::after`, and `.status-letter-wave::after`; `frontend/src/entrypoints/mission-control.test.tsx` asserts the shared gradient and `mm-status-pill-shimmer` animation across mask layers.
Rationale: The implementation no longer treats the host background and text pulse as independent primary effects. All primary layers read from the same gradient token and keyframe animation.
Alternatives considered: Keep the previous animated host background plus per-glyph pulse. Rejected because it preserves the separate-illusion model the story supersedes.
Test implications: Unit verification through CSS contract tests.

## FR-002

Decision: Mark implemented_verified; glyph-rendered labels expose a text-clipped shimmer overlay.
Evidence: `frontend/src/components/ExecutionStatusPill.tsx` adds `data-label` to `.status-letter-wave`; `frontend/src/styles/mission-control.css` uses `content: attr(data-label)`, `background-clip: text`, `-webkit-background-clip: text`, transparent text fill, and the shared moving light field on `.status-letter-wave::after`.
Rationale: The base glyph spans preserve readable text while the pseudo-element overlay exposes the shared light only through the text shape.
Alternatives considered: Use only per-glyph color animation. Rejected as primary behavior because it cannot physically clip the same moving light field to text.
Test implications: Unit CSS contract test plus entrypoint render tests that prove glyph markup and labels are present.

## FR-003 / DESIGN-REQ-005

Decision: Mark implemented_verified; active-state shimmer remains scoped to the existing status-pill selector contract.
Evidence: `frontend/src/styles/mission-control.css` scopes masks to `.status-running[data-effect="shimmer-sweep"]`, `.status-running.is-executing`, and `.status-running.is-planning`; `frontend/src/utils/executionStatusPillClasses.ts` remains the metadata boundary; task-list and task-detail tests assert active shimmer metadata on executing/planning surfaces.
Rationale: Keeping selector ownership in `executionStatusPillProps()` avoids duplicating state normalization and prevents inactive statuses from inheriting the effect.
Alternatives considered: Add a new component prop or page-local status selector. Rejected because the existing shared selector boundary is already in use.
Test implications: Unit helper/CSS tests and integration-style entrypoint render tests.

## FR-004 / DESIGN-REQ-004 Reduced Motion

Decision: Mark implemented_verified; reduced-motion users get no animated shimmer/text/glyph motion and retain a static active highlight.
Evidence: `frontend/src/styles/mission-control.css` has a `prefers-reduced-motion: reduce` block that disables animation on pseudo-element masks, text mask, and glyph fallback while setting static shimmer background positioning; CSS contract tests assert reduced-motion animation removal.
Rationale: The effect remains perceivable as active without requiring movement for comprehension.
Alternatives considered: Remove all active highlighting in reduced motion. Rejected because the spec requires active status recognition.
Test implications: Unit CSS contract test.

## FR-005 / DESIGN-REQ-002

Decision: Mark implemented_verified; shimmer motion is CSS-only after render.
Evidence: `ExecutionStatusPill.tsx` renders static spans and inline CSS custom properties only; `mission-control.css` owns repeated movement through keyframes; no JavaScript interval, timeout, requestAnimationFrame loop, or React state animation is introduced.
Rationale: CSS animation avoids repeated React renders and keeps the visual effect isolated from data refresh behavior.
Alternatives considered: JavaScript-driven synchronized animation. Rejected because it is unnecessary and conflicts with the story's CSS-only constraint.
Test implications: Unit code review plus CSS contract tests; no integration service setup.

## FR-006 / DESIGN-REQ-004 Unsupported Mask Fallback

Decision: Mark implemented_verified; unsupported text clipping falls back to the existing glyph brightening animation.
Evidence: `mission-control.css` keeps `@keyframes mm-executing-letter-brighten`; the default active glyph rule sets `animation: none`; `@supports not ((background-clip: text) or (-webkit-background-clip: text))` disables the text mask and re-enables the glyph animation.
Rationale: The fallback preserves readable active text emphasis without making the legacy pulse the primary behavior.
Alternatives considered: Drop the fallback and rely only on base text. Rejected because the spec requests unsupported mask/blend fallback behavior.
Test implications: Unit CSS contract test.

## FR-007 / DESIGN-REQ-003

Decision: Mark implemented_verified; canonical shimmer documentation states the desired shared additive light-field model.
Evidence: `docs/UI/EffectShimmerSweep.md` now states that fill, border, and text treatments are masks of the same moving light field and updates the visual model, implementation shape, acceptance criteria, and non-goals accordingly.
Rationale: Canonical docs should describe target semantics rather than a migration checklist.
Alternatives considered: Leave the documentation as optional foreground brightening. Rejected because it would conflict with the new desired state.
Test implications: Documentation review and final MoonSpec verification.

## FR-008

Decision: Mark implemented_verified; status labels, accessibility, layout, and metadata boundaries are preserved.
Evidence: `ExecutionStatusPill.tsx` preserves `aria-label={label}`, hidden visual glyph spans, grapheme splitting, and `executionStatusPillProps(status)` delegation; `tasks-list.test.tsx` and `task-detail.test.tsx` assert text content, aria labels, glyph counts, and active metadata.
Rationale: The text mask adds an overlay but does not replace the label source or status metadata source.
Alternatives considered: Render a second visible label node outside the existing glyph container. Rejected because it risks layout and accessibility drift.
Test implications: Unit/component render tests and integration-style entrypoint render tests.

## Test Tooling

Decision: Use Vitest CSS contract tests for unit coverage and Testing Library entrypoint render tests for integration-style frontend coverage; use the repo test runner for final verification.
Evidence: Existing frontend tests already import the Mission Control stylesheet through PostCSS helpers and render task list/detail pages with representative payloads.
Rationale: The story changes visual and component contracts, not backend behavior. Compose-backed hermetic integration would not add meaningful coverage.
Alternatives considered: Playwright visual screenshots. Deferred because the repository's existing coverage for this UI contract is deterministic CSS and DOM verification; screenshot coverage can be added later if visual regressions become frequent.
Test implications: Unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/mission-control.test.tsx`. Integration-style command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/task-detail.test.tsx`.
