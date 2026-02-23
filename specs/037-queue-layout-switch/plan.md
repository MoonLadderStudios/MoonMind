# Implementation Plan: Task UI Queue Layout Switching

**Branch**: `037-queue-layout-switch` | **Date**: 2026-02-23 | **Spec**: `specs/037-queue-layout-switch/spec.md`
**Input**: Feature specification derived from `docs/TaskUiQueue.md` and runtime requirements for `/tasks/queue` + "Active" dashboard views.

MoonMind needs a responsive queue layout that keeps the trusted dense table on medium-and-up viewports while adding mobile-friendly cards that reuse the exact same queue data, filters, and auto-refresh plumbing. This plan captures the shared field definition, JavaScript helpers, Tailwind rules, and validation artifacts required to ship card/table switching without feature flags.

## Summary

Render queue data through a single `queueFieldDefinitions` array, refactor the dashboard helpers to emit both a `.queue-table-wrapper` and `.queue-card-list`, drive both `/tasks/queue` and "Active" queue subsets through the shared `renderQueueLayouts`, gate cards to queue rows only, and add Tailwind utility blocks so CSS hides cards on `md+` breakpoints. Update docs/tests (`docs/TailwindStyleSystem.md`, dashboard unit specs, manual QA notes) and keep bundle growth under the 3 KB gzip target.

## Technical Context

**Language/Version**: Python 3.11 (FastAPI backend), JavaScript ES2020 (vanilla dashboard), Tailwind CSS compiled via Node 20 toolchain, pytest/node tests via `./tools/test_unit.sh`.  
**Primary Dependencies**: FastAPI view-model pipeline for dashboard config, vanilla JS utilities in `dashboard.js`, Tailwind + PostCSS build defined in `package.json`, Jest-style Node unit tests under `tests/task_dashboard`.  
**Storage**: Queue data fetched from existing `/api/queue` endpoints; no schema changes required.  
**Testing**: `./tools/test_unit.sh` orchestrates Python and dashboard JS suites; manual responsive verification at 320 px / 768 px / 1024 px per spec.  
**Target Platform**: Dockerized MoonMind stack serving `/tasks/queue` (desktop + mobile browsers).  
**Project Type**: Static dashboard (vanilla JS/CSS) backed by FastAPI JSON endpoints.  
**Performance Goals**: Preserve existing auto-refresh cadence (<5 s) and keep bundle size growth <3 KB gzip.  
**Constraints**: Must avoid duplicate API requests, cards limited to queue rows, DOM size increase <2× for ≤200 rows, CSS uses MoonMind tokens.  
**Scale/Scope**: Queue view typically handles hundreds of jobs and multiple filters; "Active" page mixes queue + orchestrator rows.

## Constitution Check

`.specify/memory/constitution.md` is template-only (no ratified principles), so no enforceable gates exist. We still document decisions, keep JS/Python lint clean, and ensure tests accompany runtime changes. Re-run this gate once a real constitution lands.

## Project Structure

### Documentation (feature artifacts)

```text
specs/037-queue-layout-switch/
├── spec.md
├── plan.md                # this document
├── research.md            # Phase 0 output
├── data-model.md          # queue row + layout view models
├── quickstart.md          # QA + verification steps
└── contracts/
    └── requirements-traceability.md
```

### Source Code & Runtime Assets

```text
docs/
└── TaskUiQueue.md                     # source doc to keep aligned

api_service/
├── static/task_dashboard/
│   ├── dashboard.js                   # queue render helpers, filters, auto-refresh
│   └── dashboard.tailwind.css         # Tailwind tokens + responsive rules
├── templates/task_dashboard.html      # ensures container classes exist (no markup churn)
└── tests/task_dashboard/
    └── queue_layouts.test.js          # extend existing JS tests or add new ones

docs/TailwindStyleSystem.md            # update tokens + bundle delta note
package.json                           # contains dashboard build/test scripts
```

**Structure Decision**: Implement entirely in-place within `api_service/static/task_dashboard` and shared docs/tests. This keeps vanilla JS templating, Tailwind build wiring, and MoonMind docs consistent; no new packages or build pipelines are needed.

## Implementation Strategy

### 1. Shared queue field definitions (FR-001)
- Introduce `queueFieldDefinitions` near `toQueueRows()` with `{ key, label, render, tableSection }` entries for queue, runtime, skill, and timeline timestamps (created/started/finished).
- Export a helper `renderQueueFieldValue(row, definition)` to centralize formatting/escaping and ensure both layouts fall back to `-` when values are missing.
- Document how downstream code extends this array when new metadata is required.

### 2. Layout helpers + wiring (FR-002–FR-006)
- Extract existing table renderer into `renderQueueTable(rows)` that iterates `queueFieldDefinitions` for `<th>/<td>` creation while keeping the legacy `renderRowsTable` available for non-queue pages.
- Add `renderQueueCards(rows)` that filters `row.source === "queue"`, composes semantic `<ul role="list">` markup, reuses status badges, and injects `View details` actions.
- Build `renderQueueLayouts(rows)` that handles empty states, adds `data-layout` hints, wraps table markup in `.queue-table-wrapper`, and only emits cards when queue rows exist. Keep orchestrator rows table-only but ensure they continue to appear in the combined markup.
- Update `renderQueueListPage()` (initial render, filter changes, auto-refresh callbacks) to inject `renderQueueLayouts(filteredRows)` so there is a single DOM subtree for both layouts.
- Update `renderActivePage()` to sort queue rows, feed them through `renderQueueLayouts`, and concatenate orchestrator/manifests tables as needed, guaranteeing cards never appear for unsupported sources.

### 3. Responsive Tailwind rules (FR-007–FR-008)
- Define `.queue-layouts`, `.queue-table-wrapper`, `.queue-card-list`, `.queue-card`, and supporting child selectors in `dashboard.tailwind.css`, using MoonMind CSS tokens and Tailwind utilities where practical. Cards use CSS grid for definition lists, flexbox for headers/actions, and respect the color palette.
- Apply breakpoint rules: hide cards on `@media (min-width:768px)` and hide tables on `@media (max-width:767px)` except when non-queue rows require sticky table visibility (use `data-sticky-table` flag).
- Ensure markup stays semantic (`role="list"`, `<dl>`, `<dt>`, `<dd>`) and `.button` variants remain accessible/focusable.
- Rebuild the dashboard bundle (document size delta in quickstart/docs) and verify gzip delta <3 KB.

### 4. Tests + validation (FR-009)
- Extend `tests/task_dashboard/` to cover `queueFieldDefinitions` (table vs card counts), `renderQueueCards`, `renderQueueLayouts` empty state, and filter re-render behavior. Use the existing Node/Jest harness invoked by `./tools/test_unit.sh` so CI sees regressions.
- Add manual QA checklist to `quickstart.md` covering Chrome DevTools responsive presets (320 px, 768 px, 1024 px), filter interactions, auto-refresh, and Active-page queue subsets.
- Run `./tools/test_unit.sh` locally before handoff.

### 5. Documentation + rollout (FR-010)
- Update `docs/TailwindStyleSystem.md` with the new classes, responsive contract, and measured bundle delta. Mention no feature flag is required.
- Capture bundle measurement methodology (e.g., `du -h api_service/static/task_dashboard/dashboard.css`) in the quickstart to keep ops and docs aligned.
- Confirm `/docs/TaskUiQueue.md` and `spec.md` remain synchronized post-implementation.

## Complexity Tracking

No constitution violations or additional projects introduced, so the tracking table remains empty.
