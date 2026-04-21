# Layout and Table Composition Contract

## Scope

This contract defines the MM-426 rendered UI structure for Mission Control task-list composition and shared dense tables.

## Task List Control Deck

The task list page exposes:

- `.task-list-control-deck.panel--controls`
- `.task-list-control-grid`
- `.task-list-utility-cluster`
- `.task-list-filter-row`
- `.task-list-filter-chip`

The control deck contains page title, workflow/status/entry/repository filters, live-update toggle and status text, active-filter chips, and a clear-filters action.

## Task List Data Slab

The task list result surface exposes:

- `.task-list-data-slab.panel--data`
- `.queue-results-toolbar`
- `.queue-table-wrapper[data-layout="table"]`
- `.queue-card-list[data-layout="card"]`

The data slab contains page summary, page-size selector, pagination controls, desktop table, and mobile cards.

## Table Posture

Desktop dense tables use a matte or near-opaque data slab. Table headers are sticky inside the scrollable table wrapper. Long identifiers wrap inside constrained cells.

Shared `DataTable` emits:

- `.data-table-slab[data-layout="table"]`
- `.data-table`
- `.data-table-empty` for the empty state

## Non-Goals

- No route ownership changes.
- No API request or response contract changes.
- No changes to Temporal status semantics.
- No broad redesign of manifests, schedules, settings, or task detail pages.
