# Feature Specification: Task UI Queue Layout Switching

**Feature Branch**: `037-queue-layout-switch`  
**Created**: 2026-02-23  
**Status**: Draft  
**Input**: User description: "Implement docs/TaskUiQueue.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Production runtime code changes plus automated validation tests are mandatory deliverables for this feature.  
**Source Document**: docs/TaskUiQueue.md (last updated 2026-02-23)

## Problem Statement & Goals
Queue operators rely on the `/tasks/queue` and "Active" dashboard views during incident triage, but the current table layout is unusable on phones and small tablets. Operators must horizontally scroll to read statuses or tap microscopic controls, so they defer mobile monitoring altogether. At the same time, desktop power users need the dense table they already trust. We must introduce a responsive queue layout that keeps the existing table for medium-and-up viewports while serving a card-first mobile experience from the exact same queue data without breaking auto-refresh, filters, or navigation.

Primary goals:
- Keep the table layout unchanged for ≥768 px screens so desktop and tablet operators retain their dense monitoring view.
- Render queue rows as tappable cards on <768 px screens, exposing the same metadata stack and a clear call-to-action for job details.
- Drive both layouts from a single queue field definition list to avoid divergent markup and ensure future metadata additions stay synchronized.
- Limit the change to CSS/markup/JS updates in `dashboard.js` and `dashboard.tailwind.css`, shipping without feature flags while keeping bundle growth negligible (<3 KB gzip).

Non-goals:
- Reworking queue detail pages, orchestrator/manifests list layouts, or API payload contracts.
- Introducing a front-end framework; the dashboard remains vanilla JS plus Tailwind-generated CSS.
- Changing how auto-refresh, telemetry summaries, or navigation events are scheduled.

## Source Document Requirements
| ID | Source Reference | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | §3 Experience Goals | Maintain the current queue table for `min-width:768px` viewports and introduce a mobile-first card layout (`max-width:767px`) that shows the same `QueueRow` information with zero behavioral regressions. |
| DOC-REQ-002 | §4.1 Shared Row Definition | Define a `queueFieldDefinitions` array near `toQueueRows()` so table columns and card fields share one source of truth for labels, ordering, and render helpers. |
| DOC-REQ-003 | §4.2 Responsive Layout Switching | Build `renderQueueLayouts(rows)` and have `renderQueueListPage()` plus `renderActivePage()` call it so filters and sorting feed both layouts simultaneously while non-queue sources remain table-only. |
| DOC-REQ-004 | §4.3 Card Composition | Implement `renderQueueCards(rows)` that outputs list items with header (title link + status badge), metadata line, definition list generated from `queueFieldDefinitions`, and a `View details` action. |
| DOC-REQ-005 | §4.4 Table Composition | Rename the existing queue table helper to `renderQueueTable(rows)`, wrap it with `.queue-table-wrapper`, and derive `<th>/<td>` cells from `queueFieldDefinitions` to keep column parity. |
| DOC-REQ-006 | §4.5 CSS / Tailwind Rules | Add `.queue-layouts`, `.queue-table-wrapper`, `.queue-card-*` classes with breakpoint rules so Tailwind builds show cards on mobile and tables on md+, using MoonMind tokenized colors. |
| DOC-REQ-007 | §4.6 Accessibility & Performance | Keep cards semantic (`<ul role="list">`, `<li>`, `<dl>`), ensure `View details` buttons use accessible roles, and limit DOM growth to <2× rows with no new network calls. |
| DOC-REQ-008 | §5-6 Implementation & Rollout | Deliver the JS + CSS refactor without template changes, validate via responsive manual testing plus `./tools/test_unit.sh`, ship without feature flags, update `docs/TaskDashboardStyleSystem.md`, and monitor bundle growth (<3 KB gzip). |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Monitor Queue From Phone (Priority: P1)
A MoonMind operator opens `/tasks/queue` on a 360 px-wide device while away from their laptop. The page renders queue rows as cards that expose status, runtime, timestamps, and a prominent "View details" control without forcing horizontal scrolling.

**Why this priority**: Mobile access is the primary motivation for the feature; without cards, operators cannot triage incidents from their phones.

**Independent Test**: Load `/tasks/queue` at ≤414 px, seed sample rows, and verify each card shows header, metadata line, timestamps, and the action button, all sourced from `queueFieldDefinitions`.

**Acceptance Scenarios**:

1. **Given** a viewport width of 414 px and several queue rows, **when** the operator loads `/tasks/queue`, **then** the DOM contains `.queue-card-list` entries (one per queue row) with title links, status badges, queue/skill metadata, definition list fields, and a `View details` link pointing to the job URL.
2. **Given** the operator applies a status filter or the queue auto-refreshes, **when** the data set changes, **then** both cards and tables update together via `renderQueueLayouts` so there is no stale layout.

---

### User Story 2 - Preserve Desktop Density (Priority: P2)
An SRE with a 1440 px monitor keeps the queue tab open while handling incidents. They must retain the existing table layout—including column order, sorting behavior, and status badges—without any new scroll or spacing regressions.

**Why this priority**: Desktop/tablet workflows are mission-critical and cannot regress while mobile gains improvements.

**Independent Test**: Compare current and new table markup at ≥1024 px; confirm column order, text truncation, and controls match prior releases and there are no new CSS regressions.

**Acceptance Scenarios**:

1. **Given** a viewport width of 1024 px, **when** the operator loads `/tasks/queue` or the "Active" dashboard, **then** `.queue-table-wrapper` is visible, `.queue-card-list` is hidden, and table headers mirror `queueFieldDefinitions` order with identical data as before the change.

---

### User Story 3 - Extend Queue Metadata Once (Priority: P3)
A product engineer needs to add a new metadata field (for example, publish mode) to the queue view. They should update `queueFieldDefinitions` in one place and see the field appear in both the desktop table and mobile cards without duplicate code.

**Why this priority**: The shared definition is the safeguard against layout drift and ensures future iterations remain sustainable.

**Independent Test**: Add a temporary field to `queueFieldDefinitions`, run unit tests, and verify both layouts render the new column/field with no additional changes.

**Acceptance Scenarios**:

1. **Given** a new entry appended to `queueFieldDefinitions`, **when** unit tests render tables and cards, **then** both layouts show the field in the same relative order and label, confirming a single source of truth.

### Edge Cases
- Zero queue rows: `renderQueueLayouts` must return the existing "No rows available" message without emitting empty card markup so the empty state renders consistently.
- Mixed row sources: when `rows` contains orchestrator or manifest rows, cards are omitted for those entries while the table still displays every row, preventing partial duplication.
- Rapid viewport resizing or auto-refresh: CSS should switch visibility instantly while JS keeps only one merged HTML blob so repeated polling does not duplicate cards or tables.
- Large result sets (≤200 queue rows): DOM size roughly doubles but remains performant; measurement is required to ensure scroll/focus behavior stays smooth on low-end mobile devices.

## Requirements *(mandatory)*

### Functional Requirements
- **FR-001**: Introduce `queueFieldDefinitions` next to `toQueueRows()` with the canonical ordering and render helpers for queue, runtime, skill, status, created, started, and finished metadata so both layouts derive labels/values from the same array. *(Maps: DOC-REQ-002)*
- **FR-002**: Replace the existing queue table helper with `renderQueueTable(rows)` that iterates `queueFieldDefinitions` to build `<th>/<td>` cells, wraps the result in `.queue-table-wrapper`, and keeps non-queue sources visible exactly as before. *(Maps: DOC-REQ-001, DOC-REQ-005)*
- **FR-003**: Implement `renderQueueCards(rows)` that filters to `row.source === "queue"`, renders `<ul class="queue-card-list" role="list">` containers, uses header/meta/definition list structure from the design, and injects a full-width `View details` button linking to each job detail URL. *(Maps: DOC-REQ-004, DOC-REQ-007)*
- **FR-004**: Create `renderQueueLayouts(rows)` that returns the empty-state paragraph when `rows.length === 0`, otherwise concatenates `.queue-table-wrapper` plus `.queue-card-list` markup, sets `data-layout` attributes for CSS targeting, and avoids duplicate API calls by reusing the same `rows` array. *(Maps: DOC-REQ-001, DOC-REQ-003)*
- **FR-005**: Update `renderQueueListPage()` (including filtering/sorting re-renders) to call `renderQueueLayouts(filteredRows)` so both layouts stay in lockstep with auto-refresh, and ensure manifest/orchestrator pages keep their prior renderers until cards are explicitly enabled. *(Maps: DOC-REQ-003)*
- **FR-006**: Update `renderActivePage()` so queue subsets render via `renderQueueLayouts(sortRows(queueRows))` while orchestrator rows continue through the existing table, preventing cards from appearing for unsupported sources. *(Maps: DOC-REQ-003)*
- **FR-007**: Extend `dashboard.tailwind.css` with `.queue-layouts`, `.queue-card-*`, `.queue-table-wrapper`, and breakpoint rules that hide cards on `min-width:768px` and hide tables on smaller widths, using MoonMind color tokens and Tailwind utilities to keep bundle growth under 3 KB gzip. *(Maps: DOC-REQ-006)*
- **FR-008**: Ensure card markup follows accessibility guidelines—semantic list/definition tags, `role="list"` on the `<ul>`, focusable `View details` buttons using `<a>` with button classes, and status badges using existing `status-*` tokens—to satisfy screen reader and keyboard navigation requirements. *(Maps: DOC-REQ-007)*
- **FR-009**: Add unit tests (or extend existing dashboard tests) that render the new helpers, prove `queueFieldDefinitions` drives both layouts, and run them via `./tools/test_unit.sh`; include responsive manual QA notes (320 px, 768 px, 1024 px) before handing off. *(Maps: DOC-REQ-008)*
- **FR-010**: Document the shipped layout in `docs/TaskDashboardStyleSystem.md`, confirm no feature flag is needed, and capture the observed dashboard bundle delta (expected <3 KB gzip) as part of release notes. *(Maps: DOC-REQ-008)*

### Key Entities *(include if feature involves data)*
- **QueueRow**: Existing normalized representation returned by `toQueueRows(items)`, supplying source, id, queueName, runtimeMode, skillId, timestamps, and action links for each queue job.
- **QueueFieldDefinition**: Array of `{ key, label, render }` objects that standardize which QueueRow properties appear in both cards and tables plus how to format them (e.g., timestamp → human-readable string).
- **QueueLayoutsViewModel**: Helper return value that combines `tableHtml`, `cardsHtml`, and empty-state text so calling pages can inject one HTML blob regardless of viewport.

### Assumptions & Dependencies
- Tailwind build tooling (documented in `docs/TaskDashboardStyleSystem.md`) remains available so `dashboard.tailwind.css` changes can be compiled into `dashboard.css`.
- `dashboard.js` continues to use vanilla JS templating; no bundler changes are required for injecting the new helpers.
- Queue endpoints already provide all fields referenced by `queueFieldDefinitions`; no API payload changes are necessary.
- Auto-refresh and filtering logic already re-render via string templates; wiring `renderQueueLayouts` into those pathways is sufficient to update both layouts simultaneously.
- DOM size increases remain manageable because queue views typically cap at ~200 rows; performance testing should confirm no frame drops on mid-tier mobile devices.

### Non-Goals
- Creating card layouts for orchestrator, manifest, or proposal queues (listed separately as future work).
- Adding row-level action buttons (Cancel/Retry) within cards until the queue API exposes capability metadata inline.
- Changing telemetry summaries, global navigation, or detail view contracts; those remain untouched by this feature.

## Success Criteria *(mandatory)*

### Measurable Outcomes
- **SC-001**: When `/tasks/queue` is viewed at ≤414 px width, 100% of queue entries render as cards showing title, status, queue name, runtime, skill, and timestamps without horizontal scrolling, verified across at least 10 sample jobs.
- **SC-002**: For ≥768 px widths, the visible table headers and cells exactly match the `queueFieldDefinitions` sequence and pre-change layout, as confirmed by snapshot/unit tests comparing rendered HTML before and after the change.
- **SC-003**: Adding/removing a field in `queueFieldDefinitions` automatically updates both card and table layouts with no other code changes, proven by an automated test that fails if the counts diverge.
- **SC-004**: `./tools/test_unit.sh` passes and includes coverage for `renderQueueLayouts`, `renderQueueTable`, and `renderQueueCards`, ensuring runtime deliverables are backed by validation tests.
- **SC-005**: After rebuilding Tailwind assets, the dashboard bundle grows by no more than 3 KB gzip (measured via `du` or bundler stats) and no feature flag is introduced, aligning with rollout guidance.

### Validation Approach
- Extend existing dashboard unit tests (or add new ones) that render helpers with mock rows to assert shared definitions, mobile/desktop toggles, and empty-state handling.
- Perform manual responsive verification at 320 px, 768 px, and 1024 px breakpoints to confirm CSS switches and auto-refresh/filter flows remain intact.
- Capture before/after bundle sizes and document the observed delta plus Tailwind doc update as part of release notes.
