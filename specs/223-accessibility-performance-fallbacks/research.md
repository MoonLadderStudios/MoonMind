# Research: Mission Control Accessibility, Performance, and Fallback Posture

## FR-001 / DESIGN-REQ-015

Decision: Treat contrast posture as implemented but insufficiently verified for MM-429. Add explicit CSS contract tests for representative contrast-bearing selectors.  
Evidence: `frontend/src/styles/mission-control.css` defines `--mm-ink`, `--mm-muted`, `--mm-panel`, `--mm-input-well`, `--mm-control-shell`, and glass/panel tokens; existing `mission-control.test.tsx` checks shared visual tokens but not the full MM-429 contrast list.  
Rationale: Current CSS appears token-based and readable, but the Jira story names labels, table text, placeholder text, chips, buttons, focus states, and glass-over-gradient surfaces as acceptance targets.  
Alternatives considered: Manual visual review only; rejected because the story requires durable verification evidence.  
Test implications: Unit/CSS contract tests first; implementation only if a selector lacks an appropriate readable color/background/focus token.

## FR-002 / DESIGN-REQ-022

Decision: Treat focus-visible coverage as implemented_unverified and add representative all-surface tests.  
Evidence: `frontend/src/styles/mission-control.css` contains `input/select/textarea:focus-visible`, `button:focus-visible`, `.button:focus-visible`, `.table-sort-button:focus-visible`, `.queue-action:focus-visible`, `.queue-submit-primary:focus-visible`, `.step-tl-toggle:focus-visible`, `.live-logs-artifact-link:focus-visible`, `.queue-step-attachment-add-button:focus-visible`, and `.td-instructions-toggle:focus-visible`.  
Rationale: The selectors exist, but MM-429 needs explicit evidence that representative interactive surface families are covered.  
Alternatives considered: Adding new global `*:focus-visible` rules; rejected unless tests reveal a concrete uncovered surface, because broad rules can disturb existing component focus styling.  
Test implications: CSS contract test first; small selector additions if gaps appear.

## FR-003 / FR-008 / DESIGN-REQ-006

Decision: Treat reduced-motion posture as partial. Extend tests and CSS for nonessential live effects beyond routine controls.  
Evidence: `@media (prefers-reduced-motion: reduce)` currently suppresses routine controls, but `.step-tl-icon.step-icon-running` still has `animation: step-pulse 1.8s ease-in-out infinite` outside the reduced-motion block.  
Rationale: The Jira brief explicitly names pulses, shimmer, scanner effects, and highlight drift; running step pulse is a visible live-state pulse and must be removed or significantly softened.  
Alternatives considered: Leaving the pulse as a low-amplitude live-state effect; rejected because the source design says reduced-motion mode is required and should remove or significantly soften pulses.  
Test implications: Red-first CSS test for reduced-motion suppression of `.step-tl-icon.step-icon-running` and other premium/live selectors, then CSS update.

## FR-004 / DESIGN-REQ-003 / DESIGN-REQ-022

Decision: Treat backdrop-filter fallback as implemented_unverified and add focused contract coverage.  
Evidence: `frontend/src/styles/mission-control.css` has `@supports not ((backdrop-filter: blur(2px)) or (-webkit-backdrop-filter: blur(2px)))` fallback for `.surface--glass-control`, `.panel--controls`, `.panel--floating`, `.panel--utility`, `.surface--liquidgl-hero`, and `.queue-floating-bar`.  
Rationale: The fallback exists and matches the design direction, but MM-429 needs explicit traceable evidence that fallback surfaces remain coherent.  
Alternatives considered: Browser-specific E2E simulation; rejected for this story because the repo already uses CSS contract tests and JSDOM cannot reliably emulate CSS feature support.  
Test implications: CSS contract test, plus final route regression.

## FR-005 / DESIGN-REQ-003 / DESIGN-REQ-022

Decision: Treat liquidGL fallback as implemented_unverified and preserve existing CSS shell/hook behavior.  
Evidence: `.queue-floating-bar--liquid-glass` is a CSS-complete fixed floating bar before `data-liquid-gl-initialized="true"` is set; `frontend/src/lib/liquidGL/useLiquidGL.ts` returns without mutation when `getLiquidGL()` is unavailable and catches initialization errors. Existing tests cover cleanup and create-page shell presence.  
Rationale: The system has a progressive enhancement model; MM-429 needs focused evidence that disabled/unavailable liquidGL leaves complete layout and controls.  
Alternatives considered: Making liquidGL mandatory; rejected by source design fallback requirements.  
Test implications: CSS and existing hook/create tests; implementation only if shell/fallback evidence fails.

## FR-006 / FR-007 / DESIGN-REQ-023

Decision: Preserve opt-in premium effects and dense-region matte posture with stronger MM-429 tests.  
Evidence: Existing tests assert liquidGL is absent from `.panel`, `.card`, `table`, and `.data-table-slab`; MM-428 added matte evidence/detail classes for dense regions.  
Rationale: Heavy effects already appear limited, but the story needs explicit traceability for dense reading, table, form, evidence, log, and editing regions.  
Alternatives considered: Removing all glass/liquid effects; rejected because the source design allows strategic premium surfaces.  
Test implications: CSS tests first; no implementation unless selectors show premium effects on dense regions.

## FR-009

Decision: Preserve existing behavior by running focused task-list/create/detail regression tests after CSS changes.  
Evidence: Existing Vitest files cover task-list rendering/filtering/pagination, create-page submission controls, task-detail evidence/log regions, and liquidGL hook behavior.  
Rationale: This story is visual resilience and must not change payloads, routes, or backend behavior.  
Alternatives considered: New backend integration tests; rejected because backend contracts are unchanged.  
Test implications: Targeted UI tests, then final unit wrapper if feasible.

## FR-010 / FR-011

Decision: Add MM-429-specific test names and preserve traceability in all artifacts.  
Evidence: `docs/tmp/jira-orchestration-inputs/MM-429-moonspec-orchestration-input.md` and `spec.md` preserve the trusted Jira brief. No MM-429-specific implementation tests exist yet.  
Rationale: Jira Orchestrate and MoonSpec verification need artifact-level and test-level evidence for the issue key and source design IDs.  
Alternatives considered: Relying on adjacent MM-427/MM-428 tests only; rejected because MM-429 is a distinct story with reduced-motion/fallback/performance acceptance criteria.  
Test implications: Add focused test cases and final verification evidence.
