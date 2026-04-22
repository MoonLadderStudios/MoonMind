# Research: Mission Control Accessibility, Performance, and Fallback Posture

## FR-001 / DESIGN-REQ-015

Decision: Contrast posture required explicit MM-429 CSS contract tests for representative contrast-bearing selectors; this is now implemented and verified.
Evidence: `frontend/src/styles/mission-control.css` defines readable token usage for labels, table text, placeholders, chips, buttons, focus states, and glass surfaces; `frontend/src/entrypoints/mission-control.test.tsx` includes MM-429 contrast coverage.
Rationale: Current CSS appears token-based and readable, but the Jira story names labels, table text, placeholder text, chips, buttons, focus states, and glass-over-gradient surfaces as acceptance targets.
Alternatives considered: Manual visual review only; rejected because the story requires durable verification evidence.
Test implications: Unit/CSS contract tests first, followed by selector updates where gaps were exposed.

## FR-002 / DESIGN-REQ-022

Decision: Focus-visible coverage required representative all-surface tests; this is now implemented and verified.
Evidence: `frontend/src/styles/mission-control.css` contains focus-visible coverage for inputs, buttons, route navigation, table sort controls, task controls, live-log links, attachment controls, and task-detail toggles; `frontend/src/entrypoints/mission-control.test.tsx` includes MM-429 focus coverage.
Rationale: The selectors exist, but MM-429 needs explicit evidence that representative interactive surface families are covered.
Alternatives considered: Adding new global `*:focus-visible` rules; rejected unless tests reveal a concrete uncovered surface, because broad rules can disturb existing component focus styling.
Test implications: CSS contract test first, followed by small selector additions where gaps appeared.

## FR-003 / FR-008 / DESIGN-REQ-006

Decision: Reduced-motion posture required explicit tests and CSS for nonessential live effects beyond routine controls; this is now implemented and verified.
Evidence: `frontend/src/entrypoints/mission-control.test.tsx` includes MM-429 reduced-motion assertions; `frontend/src/styles/mission-control.css` suppresses `.step-tl-icon.step-icon-running` and premium-surface animation in `@media (prefers-reduced-motion: reduce)`.
Rationale: The Jira brief explicitly names pulses, shimmer, scanner effects, and highlight drift; running step pulse is a visible live-state pulse and must be removed or significantly softened.
Alternatives considered: Leaving the pulse as a low-amplitude live-state effect; rejected because the source design says reduced-motion mode is required and should remove or significantly soften pulses.
Test implications: Red-first CSS test for reduced-motion suppression of `.step-tl-icon.step-icon-running` and other premium/live selectors, followed by CSS update and final passing validation.

## FR-004 / DESIGN-REQ-003 / DESIGN-REQ-022

Decision: Backdrop-filter fallback required focused MM-429 contract coverage; this is now implemented and verified.
Evidence: `frontend/src/styles/mission-control.css` has `@supports not ((backdrop-filter: blur(2px)) or (-webkit-backdrop-filter: blur(2px)))` fallback for `.surface--glass-control`, `.panel--controls`, `.panel--floating`, `.panel--utility`, `.surface--liquidgl-hero`, and `.queue-floating-bar`; `frontend/src/entrypoints/mission-control.test.tsx` verifies the fallback shell.
Rationale: The fallback exists and matches the design direction, but MM-429 needs explicit traceable evidence that fallback surfaces remain coherent.
Alternatives considered: Browser-specific E2E simulation; rejected for this story because the repo already uses CSS contract tests and JSDOM cannot reliably emulate CSS feature support.
Test implications: CSS contract test, plus final route regression.

## FR-005 / DESIGN-REQ-003 / DESIGN-REQ-022

Decision: liquidGL fallback required explicit CSS shell and hook behavior tests; this is now implemented and verified.
Evidence: `.queue-floating-bar--liquid-glass` is a CSS-complete fixed floating bar before `data-liquid-gl-initialized="true"` is set; `frontend/src/lib/liquidGL/useLiquidGL.ts` returns without mutation when `getLiquidGL()` is unavailable and catches initialization errors; `frontend/src/entrypoints/task-create.test.tsx` and `frontend/src/lib/liquidGL/useLiquidGL.test.tsx` verify fallback behavior.
Rationale: The system has a progressive enhancement model; MM-429 needs focused evidence that disabled/unavailable liquidGL leaves complete layout and controls.
Alternatives considered: Making liquidGL mandatory; rejected by source design fallback requirements.
Test implications: CSS and hook/create tests, followed by implementation only where shell/fallback evidence failed.

## FR-006 / FR-007 / DESIGN-REQ-023

Decision: Preserve opt-in premium effects and dense-region matte posture with stronger MM-429 tests; this is now implemented and verified.
Evidence: `frontend/src/entrypoints/mission-control.test.tsx` verifies liquidGL and heavy premium effects remain absent from dense/table/evidence/editing selectors; MM-428 task detail/evidence classes remain in place.
Rationale: Heavy effects already appear limited, but the story needs explicit traceability for dense reading, table, form, evidence, log, and editing regions.
Alternatives considered: Removing all glass/liquid effects; rejected because the source design allows strategic premium surfaces.
Test implications: CSS tests first; implementation only if selectors showed premium effects on dense regions.

## FR-009

Decision: Preserve existing behavior by running focused task-list/create/detail regression tests after CSS changes; this is now verified.
Evidence: Focused route regression tests passed for task-list rendering/filtering/pagination, create-page submission controls, task-detail evidence/log regions, and liquidGL hook behavior.
Rationale: This story is visual resilience and must not change payloads, routes, or backend behavior.
Alternatives considered: New backend integration tests; rejected because backend contracts are unchanged.
Test implications: Targeted UI tests, then final unit wrapper if feasible.

## FR-010 / FR-011

Decision: Add MM-429-specific test names and preserve traceability in all artifacts. This is now implemented and verified.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-429-moonspec-orchestration-input.md`, `spec.md`, `tasks.md`, and `verification.md` preserve the trusted Jira brief. MM-429-specific tests exist in `frontend/src/entrypoints/mission-control.test.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, and `frontend/src/lib/liquidGL/useLiquidGL.test.tsx`.
Rationale: Jira Orchestrate and MoonSpec verification need artifact-level and test-level evidence for the issue key and source design IDs.
Alternatives considered: Relying on adjacent MM-427/MM-428 tests only; rejected because MM-429 is a distinct story with reduced-motion/fallback/performance acceptance criteria.
Test implications: Add focused test cases and final verification evidence.
