# Feature Specification: Tailwind Style System Phase 2

**Feature Branch**: `025-tailwind-dashboard`  
**Created**: 2026-02-18  
**Status**: Draft  
**Input**: Implement phase 2 of the Tailwind Style System for the tasks dashboard (tokenize colors, purple/cyan gradients, brand alignment).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operators see branded UI without regressions (Priority: P1)

MoonMind operators should load `/tasks` or any dashboard route and see the new purple-forward palette, gradients, and chips rendered via the shared CSS without degraded readability or layout changes.

**Why this priority**: The dashboard is a production tool; any visual rewrite must preserve high legibility for day-to-day monitoring.

**Independent Test**: Open `/tasks`, `/tasks/queue`, and `/tasks/orchestrator` in a browser, visually confirm palette + gradients update, compare before/after screenshots to ensure typography/layout spacing remain stable.

**Acceptance Scenarios**:

1. **Given** the dashboard assets have been rebuilt, **When** an operator views `/tasks`, **Then** backgrounds, panels, navigation pills, and status chips use the MoonMind purple palette with no console errors.
2. **Given** the operator resizes the viewport, **When** they move between consolidated and detail pages, **Then** semantic class selectors still apply (no missing styles or fallback plain HTML).

---

### User Story 2 - Developers manage tokens centrally (Priority: P1)

Front-end maintainers should be able to adjust brand colors, gradients, and status hues through a consistent `--mm-*` token set rather than bespoke variables scattered across CSS.

**Why this priority**: Tokenization is the precursor for Tailwind adoption and future dark-mode; it reduces ongoing maintenance costs.

**Independent Test**: Inspect the generated CSS and confirm all color/gradient declarations reference `--mm-*` tokens; toggling token values in DevTools should cascade to dependent components.

**Acceptance Scenarios**:

1. **Given** the new CSS is deployed, **When** a developer searches for the previous `--accent`/`--bg` tokens, **Then** they are absent and replaced with documented `--mm-*` tokens.
2. **Given** the developer overrides `--mm-accent` in DevTools, **When** they refresh, **Then** buttons, nav pills, and status badges all respond uniformly.

---

### User Story 3 - Build + docs alignment (Priority: P2)

Engineering enablement teams need the implementation to match the guidance in `docs/TailwindStyleSystem.md`, including purple/cyan/pink gradients and status color mappings so later phases can build on a consistent baseline.

**Why this priority**: Documentation loses credibility if the live dashboard diverges; keeping doc + assets in sync unblocks downstream Tailwind phases.

**Independent Test**: Compare the final CSS variables/colors/background values against the palette described in the doc and confirm the validation checklist is updated.

**Acceptance Scenarios**:

1. **Given** the Tailwind Style System doc describes the palette, **When** auditors compare doc values to CSS, **Then** the hues and naming match.
2. **Given** CI rebuilds `dashboard.css`, **When** CSS diffs are reviewed, **Then** only the documented palette/token changes appear (no unrelated rewrites).

---

### Edge Cases

- What happens if a browser does not understand CSS variables? The dashboard must retain readable fallback colors through computed values compiled by Tailwind/Tokens.
- How does the system handle cached `dashboard.css` assets? Versioned builds or forced reload instructions should accompany release notes to avoid mixed palettes.
- Ensure gradients and tokens are performant on low-end devices; overly saturated colors should not reduce text contrast below WCAG AA.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dashboard stylesheet MUST replace legacy `--bg`, `--accent`, and related CSS variables with the `--mm-*` token set defined in the Tailwind Style System doc, covering base surfaces, accents, status colors, and shadows.
- **FR-002**: The body background MUST use the documented purple/cyan/pink layered gradients so the dashboard presents the futuristic “liquid glass” brand direction.
- **FR-003**: All semantic components referenced by `dashboard.js` (masthead, nav, panels, cards, tables, status chips, buttons) MUST read from the new token names to prevent drift when Tailwind begins compiling them.
- **FR-004**: The generated CSS MUST remain at `/static/task_dashboard/dashboard.css` and be reproducible via the Tailwind build scripts introduced in Phase 1; direct manual edits are no longer allowed.
- **FR-005**: QA validation MUST include a visual sweep across consolidated/list/detail pages and ensure perceived contrast for interactive elements meets WCAG AA.
- **FR-006**: Implementation MUST document any remaining TODOs for dark-mode or Tailwind compilation so Phase 3 contributors have clear next steps.

### Key Entities *(include if feature involves data)*

- **Theme Tokens**: Logical variables (`--mm-bg`, `--mm-panel`, `--mm-ink`, `--mm-accent`, status tokens, shadow) that the CSS and future Tailwind config reference; each token controls multiple UI elements.
- **Dashboard Surfaces**: Semantic class targets such as `.masthead`, `.panel`, `.route-nav a`, `.status-*`, `.card`, and `.button` that consume the tokens to render consistent glass, gradients, and states.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of color declarations in `api_service/static/task_dashboard/dashboard.css` originate from `--mm-*` tokens (verified via lint/search).
- **SC-002**: Screenshot comparison shows no readability regression and at least 90% of interactive components display purple/cyan/pink brand accents after deployment.
- **SC-003**: Tailwind Style System doc references exactly match the implemented RGB values (within ±2 units per channel) for base/accent/status tokens.
- **SC-004**: Implementation diff contains only CSS/documentation changes scoped to the dashboard styling plus supporting build metadata, confirmed via `./tools/test_unit.sh` passing (no runtime regressions).
