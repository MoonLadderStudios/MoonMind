# Feature Specification: Tailwind Style System Phase 3 Dark Mode

**Feature Branch**: `026-tailwind-dark-mode`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: User description: "Implement phase 3 of docs/TailwindStyleSystem"

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/TailwindStyleSystem.md:231` | The dashboard must define and apply a full dark-theme token override set so dark mode has distinct background, surface, text, border, accent, status, and shadow values. |
| DOC-REQ-002 | `docs/TailwindStyleSystem.md:313` | The dashboard must provide a user-facing theme toggle control within the dashboard shell. |
| DOC-REQ-003 | `docs/TailwindStyleSystem.md:320` | Theme selection must prioritize explicit user preference over system preference. |
| DOC-REQ-004 | `docs/TailwindStyleSystem.md:347` | When no explicit user preference exists, the dashboard must follow system color-scheme preference changes. |
| DOC-REQ-005 | `docs/TailwindStyleSystem.md:357` | The selected theme must be applied before first visual paint to avoid theme flash. |
| DOC-REQ-006 | `docs/TailwindStyleSystem.md:307` | The dashboard viewport metadata must include safe-area support for modern mobile displays. |
| DOC-REQ-007 | `docs/TailwindStyleSystem.md:573` | Dark mode must preserve readability for tables, forms, and live output surfaces. |
| DOC-REQ-008 | `docs/TailwindStyleSystem.md:574` | Dark mode must keep purple as the primary accent while using yellow/orange highlights in a restrained, intentional way. |
| DOC-REQ-009 | `docs/TailwindStyleSystem.md:133` | Theme behavior must remain token-driven so semantic dashboard surfaces adapt consistently by theme. |
| DOC-REQ-010 | `docs/TailwindStyleSystem.md:532` | Phase 3 scope must include dark token overrides, theme toggle + preference persistence, and no-flash theme boot behavior. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operators control and persist dark mode (Priority: P1)

MoonMind operators can switch between light and dark modes from the dashboard shell and keep their chosen theme across dashboard pages and subsequent visits.

**Why this priority**: Operators use the dashboard continuously; inability to control brightness and persist preference degrades usability and trust.

**Independent Test**: Open `/tasks`, select a theme, navigate to `/tasks/queue` and `/tasks/orchestrator`, reload each page, and confirm the selected theme persists and remains active.

**Acceptance Scenarios**:

1. **Given** an operator is viewing any dashboard route, **When** they change the theme using the dashboard control, **Then** the dashboard updates to the chosen theme without breaking page content.
2. **Given** an operator has selected a theme, **When** they reload or revisit dashboard routes, **Then** the previously selected theme is restored.

---

### User Story 2 - Default theme follows system preference without flash (Priority: P1)

Operators who have not chosen a theme manually should get an initial theme that matches their device preference, including on first load and preference changes.

**Why this priority**: Correct defaults and clean first paint are required for a polished, modern dashboard experience.

**Independent Test**: Clear saved theme preference, load `/tasks` with system light and dark settings, and verify the first rendered frame matches system preference; then change system preference and verify the dashboard updates when no explicit choice is saved.

**Acceptance Scenarios**:

1. **Given** no saved user preference exists, **When** an operator opens a dashboard route, **Then** the initial rendered theme matches current system preference.
2. **Given** no saved user preference exists and the dashboard is open, **When** system preference changes, **Then** the dashboard theme updates to match the new preference.

---

### User Story 3 - Dark mode stays readable and brand-consistent (Priority: P2)

In dark mode, key operational surfaces remain readable and the color hierarchy preserves MoonMind’s purple-first brand while using warm highlights only for attention states.

**Why this priority**: Visual style must improve modernity without reducing operational clarity.

**Independent Test**: Run a visual sweep on `/tasks`, `/tasks/queue`, and `/tasks/orchestrator` in dark mode, validating readability of tables, forms, and live output, and verifying accent usage follows the documented hierarchy.

**Acceptance Scenarios**:

1. **Given** dark mode is active, **When** an operator reviews table rows, form controls, and live output content, **Then** text and interactive states remain clearly readable.
2. **Given** dark mode is active, **When** an operator reviews primary actions and status indicators, **Then** purple remains the dominant accent and yellow/orange appears only for warning or high-attention emphasis.

---

### Edge Cases

- What happens when stored theme preference is unavailable or blocked? The dashboard should still apply a deterministic theme using system preference.
- How does the system handle rapid repeated theme toggles? The final selected state should be stable and persisted correctly.
- What happens when system preference changes during an active session after a user has explicitly selected a theme? The explicit user preference should remain authoritative.
- How does the dashboard behave when safe-area viewport support is ignored by the browser? Layout remains usable with no hidden core controls.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dashboard MUST provide a complete dark-mode theme set that defines all core semantic color roles (background, surface, text, muted text, border, accent family, status colors, and depth/shadow). (Maps: DOC-REQ-001, DOC-REQ-009)
- **FR-002**: The dashboard MUST include a user-accessible theme control that allows switching between light and dark mode from the dashboard shell on every dashboard route. (Maps: DOC-REQ-002)
- **FR-003**: The system MUST persist an operator’s explicit theme choice and restore it on subsequent dashboard visits and reloads. (Maps: DOC-REQ-003, DOC-REQ-010)
- **FR-004**: When no explicit user choice exists, the dashboard MUST follow the system color-scheme preference and update if that preference changes while the dashboard is open. (Maps: DOC-REQ-003, DOC-REQ-004)
- **FR-005**: The initial page render MUST apply the resolved theme before first visual paint to prevent light/dark flash artifacts. (Maps: DOC-REQ-005, DOC-REQ-010)
- **FR-006**: Dashboard viewport metadata MUST support safe-area rendering for modern mobile displays. (Maps: DOC-REQ-006)
- **FR-007**: The dashboard MUST ensure dark-mode contrast of at least 4.5:1 for normal text and 3:1 for large text or essential UI indicators across tables, forms, and live output on `/tasks`, `/tasks/queue`, and `/tasks/orchestrator`. (Maps: DOC-REQ-007)
- **FR-008**: In dark mode, primary interactive controls (primary buttons, active nav items, awaiting-action status) MUST use purple accent tokens, and yellow/orange tokens MUST only be used for warning/high-attention states. (Maps: DOC-REQ-008)
- **FR-009**: Theme behavior MUST remain token-driven across existing semantic dashboard surfaces so that theme changes propagate consistently without altering core route structure. (Maps: DOC-REQ-009)
- **FR-010**: The delivered scope MUST satisfy all Phase 3 dark-mode deliverables from the source document and exclude unrelated Phase 4/5 expansions except where needed to avoid regressions. (Maps: DOC-REQ-010)
- **FR-011**: The release verification for this feature MUST include evidence that dark-mode release-gate checks pass for theme persistence, no-flash initial render, readability, and accent hierarchy. (Maps: DOC-REQ-007, DOC-REQ-008, DOC-REQ-010)

### Key Entities *(include if feature involves data)*

- **Theme Preference**: A user-level selection state with values `light`, `dark`, or `unset`, where explicit selection has precedence over system-derived defaults.
- **Theme Token Set**: A named set of semantic visual roles for light and dark presentations that drives all route-level surfaces.
- **Resolved Theme State**: The active theme computed at runtime from the precedence rules (explicit user preference first, system preference second).
- **Accent Role Group**: A set of accent-intent categories (primary, secondary/live, warning/high-attention, error, success) used to preserve consistent visual hierarchy.

## Assumptions

- Phase 3 applies to dashboard routes under `/tasks` and does not include broader site theming.
- Existing dashboard information architecture and route contracts remain unchanged.
- Users may operate in environments where system preference is available but no explicit theme preference has been set.

## Dependencies

- Existing tokenized style baseline established in Phases 1-2.
- Existing semantic class model used by dashboard-rendered surfaces.
- Source document requirements in `docs/TailwindStyleSystem.md` remain the governing contract for Phase 3 scope.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of audited dashboard routes (`/tasks`, `/tasks/queue`, `/tasks/orchestrator`) allow operators to switch themes and retain the chosen theme after reload.
- **SC-002**: Across 40 hard-refresh runs with no saved theme preference (20 system-light and 20 system-dark), at least 38 runs (95%) render the correct theme on first frame.
- **SC-003**: 100% of audited tables, forms, and live-output surfaces meet the contrast thresholds defined in FR-007 in dark mode.
- **SC-004**: 100% of audited primary actions use purple-accent tokens, and 0 audited non-warning components use yellow/orange accents in dark mode.
