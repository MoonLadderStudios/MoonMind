# Research: Mission Control Styling Source and Build Invariants

## FR-001 / DESIGN-REQ-024

Decision: Stable semantic class behavior is implemented in source but needs MM-430-specific verification.
Evidence: `api_service/templates/react_dashboard.html` includes `dashboard-root` and `masthead`; `api_service/templates/_navigation.html` includes `route-nav`; `frontend/src/styles/mission-control.css` defines shared `panel`, `card`, `toolbar`, `status-*`, and `queue-*` surfaces.
Rationale: The source design requires compatibility names to remain stable. Existing tests cover layout and visual behavior, but not this invariant as a traceable MM-430 requirement.
Alternatives considered: Manual review only; rejected because MM-430 asks for durable build/style invariants.
Test implications: Add unit/CSS contract tests that verify representative semantic shell selectors and rendered template classes remain present.

## FR-002 / DESIGN-REQ-024

Decision: Additive modifier behavior appears implemented and needs explicit verification.
Evidence: `frontend/src/styles/mission-control.css` defines `panel--controls`, `panel--data`, `panel--floating`, `panel--utility`, and table/data width variants; existing MM-429 tests inspect some of these selectors.
Rationale: The invariant is not just that modifiers exist, but that shared extensions remain additive rather than replacing base shell classes.
Alternatives considered: Renaming existing data-wide classes to match examples exactly; rejected because the spec allows equivalent additive modifier patterns and existing class names are already part of the surface.
Test implications: Add tests confirming key modifier selectors coexist with base shell semantics.

## FR-003 / FR-004 / DESIGN-REQ-025

Decision: Token-first theming is mostly implemented; add role-boundary tests before changing CSS.
Evidence: `frontend/src/styles/mission-control.css` defines `--mm-*` tokens for theme, glass, controls, status, and surfaces, and existing tests assert shared chrome uses token variables. Raw RGB/hex values remain for terminal/log/code and specialized status accents.
Rationale: The source design forbids hardcoded opaque colors for tokenized roles, not every numeric color. Tests must distinguish semantic shell role styling from intentional code/log or transparent effect values.
Alternatives considered: Rewriting all raw colors to tokens; rejected because non-role terminal/log styling may intentionally use fixed contrast values and broad rewriting would exceed the story.
Test implications: Add PostCSS tests for representative semantic role selectors, allowing non-role exceptions; implementation contingency updates any failing role declarations to `--mm-*` tokens.

## FR-005 / DESIGN-REQ-025

Decision: Tailwind source scanning appears implemented but needs direct MM-430 coverage.
Evidence: `tailwind.config.cjs` includes `./api_service/templates/react_dashboard.html`, `./api_service/templates/_navigation.html`, and `./frontend/src/**/*.{js,jsx,ts,tsx}`.
Rationale: The design system names these paths as required because CSS is built before Vite output exists.
Alternatives considered: Testing Vite config only; rejected because Tailwind scanning is configured separately.
Test implications: Add a Vitest or Node-level test that loads `tailwind.config.cjs` and asserts the required content entries.

## FR-006 / FR-007 / DESIGN-REQ-026

Decision: Canonical source and generated asset boundaries need explicit verification.
Evidence: `frontend/src/styles/mission-control.css` is the source stylesheet; `frontend/vite.config.ts` builds to `api_service/static/task_dashboard/dist`; existing UI asset tests verify manifest resolution but not authoring-source boundaries.
Rationale: MM-430 specifically protects against hand-edited generated build artifacts.
Alternatives considered: Git-hook enforcement; rejected because no hook framework is required for this story and tests are sufficient for current scope.
Test implications: Add tests that document source path and dist path boundaries, and verify no task modifies generated dist assets.

## FR-008 / FR-010

Decision: Preserve traceability in artifacts and add MM-430-specific test names.
Evidence: `spec.md` (Input) and `specs/225-preserve-styling-invariants/spec.md` preserve MM-430 and source IDs.
Rationale: MoonSpec verification and PR review need clear issue-key evidence.
Alternatives considered: Rely on adjacent MM-429 tests; rejected because MM-430 is a distinct story with different invariants.
Test implications: Add tests whose names or assertions reference MM-430 invariant coverage and preserve traceability in tasks and verification.

## FR-009

Decision: Existing Mission Control behavior should be protected by focused route regression tests after invariant tests pass.
Evidence: Existing UI tests cover task list, create page, task detail/evidence, and shared Mission Control entry behavior.
Rationale: Styling/source invariant tests should not change payloads, routes, backend behavior, or user workflows.
Alternatives considered: Compose-backed integration tests; rejected because this story has no backend or persistence contract changes.
Test implications: Run focused Vitest route regression tests, then the repository unit wrapper if feasible.
