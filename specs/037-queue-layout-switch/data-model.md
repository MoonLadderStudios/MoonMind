# Data Model: Task UI Queue Layout Switching

## 1. `QueueRow` (existing)

| Field | Type | Source | Notes |
| --- | --- | --- | --- |
| `source` | string (`"queue"`, `"manifests"`, `"orchestrator"`) | `toQueueRows()` and other row normalizers | Drives filtering; only `source === "queue"` gets cards in this feature.
| `sourceLabel` | string | Normalizers | Display label for table Source column.
| `id` | string | Queue API `id` | Used for detail links + card title suffix.
| `queueName` | string | Queue API payload | Defaults to `system.defaultQueue` when missing.
| `runtimeMode` | string | Queue payload | Rendered via existing `renderRuntime()` helper.
| `skillId` | string | Queue payload | Optional; shown in metadata line and definition list.
| `rawStatus` | string | Queue payload | Feeds status badge + raw text.
| `title` | string | Summarized from instructions | Forms table Title column and card heading.
| `createdAt` | ISO timestamp | Queue payload | `formatTimestamp` handles display.
| `startedAt` | ISO timestamp | Queue payload | Optional timeline column/field.
| `finishedAt` | ISO timestamp | Queue payload | Optional timeline column/field.
| `link` | string | Derived | `/tasks/queue/:id` used by both table + card actions.

## 2. `QueueFieldDefinition`

| Field | Type | Purpose |
| --- | --- | --- |
| `key` | string | Identifier reused as `data-field` attribute on table/cell nodes.
| `label` | string | Human-readable label for `<th>` text and `<dt>` entries.
| `render(row)` | function | Returns safe HTML string (already escaped) for `<td>` or `<dd>` value; centralizes formatting.
| `tableSection` | enum (`"primary"`, `"timeline"`) | Lets the table renderer preserve the legacy column grouping (primary fields before status/title vs timeline columns).

Definition order determines both table column sequence and card field stacking, satisfying DOC-REQ-002/FR-001. Extensions append to the array (e.g., `publishMode`).

## 3. `QueueLayoutsViewModel`

This is the conceptual JS return of `renderQueueLayouts(rows)`:

| Field | Type | Notes |
| --- | --- | --- |
| `tableHtml` | string | Output of `renderQueueTable(rows)`, wrapped inside `.queue-table-wrapper` with `data-layout="table"` and `data-sticky-table` hint.
| `cardsHtml` | string | `<ul class="queue-card-list" data-layout="card" role="list">…</ul>` generated only when queue rows exist.
| `emptyHtml` | string | The prior `<p class='small'>No rows available.</p>` reused unchanged when `rows.length === 0`.

Although implemented as string templates, treating the helper as a view model clarifies responsibilities for tests and documentation.

## 4. `QueueCard` markup contract

Each queue row maps to the following semantic structure:

| Section | Elements | Description |
| --- | --- | --- |
| Header | `<div class="queue-card-header">` containing `<a.queue-card-title>` and `<p.queue-card-meta>` | Displays ID/title and queue/skill metadata line while keeping the header single-column on mobile. |
| Definition list | `<dl class="queue-card-fields">` starting with a fixed `Status` `<dt>/<dd>` row, then repeated `<div><dt>label</dt><dd>value</dd></div>` pairs iterated from `queueFieldDefinitions` | Keeps status as the first card field and preserves parity with table columns for remaining values. |
| Actions | `<div class="queue-card-actions"> <a class="button secondary" href="row.link">View details</a> </div>` | Primary CTA to job detail page; future buttons can append here without altering card skeleton. |

## 5. CSS/Breakpoint metadata

| Token/Data Attribute | Type | Usage |
| --- | --- | --- |
| `.queue-layouts` | CSS grid container | Wraps both layouts; ensures consistent spacing/topology.
| `.queue-table-wrapper[data-sticky-table]` | Attribute flag | When `true`, CSS keeps the table visible even on mobile to surface non-queue sources.
| `.queue-card-list[data-layout="card"]` | Semantic hint | Allows future JS/tests to assert that cards are rendered even when hidden on desktop.
| `.queue-card-status-field` | Status wrapper class | Enforces vertical stacking (`badge` above `raw status`) after status moved into card definition list.
| Media queries | `@media (min-width:768px)` / `@media (max-width:767px)` | Toggle visibility per DOC-REQ-001; identical breakpoints documented in TailwindStyleSystem.md.

These structures ensure layout switching remains data-driven and extensible.
