# Task UI Queue Layout Switching

Status: Proposed
Owners: MoonMind Task UI
Last Updated: 2026-02-23

## 1. Purpose

Define the responsive layout contract for queue list surfaces rendered from `api_service/static/task_dashboard/dashboard.js`. The goal is to keep the existing dense table for desktop/tablet viewports (md and up) while introducing a mobile-first card layout fed by the same `QueueRow` data returned by `toQueueRows()`.

This document scopes the queue list (`/tasks/queue`) and the queue portions of the "Active" dashboard. Detail pages keep their current layout.

## 2. Background / Current State

- `renderRowsTable(rows)` renders the only queue list layout today. It is injected by `renderQueueListPage()` (queue tab), `renderActivePage()` (queue + orchestrator mashup), and manifest/orchestrator list renderers.
- On phone viewports the table forces horizontal scrolling, truncates status badges, and makes row-level controls hard to discover.
- Tailwind tokens and `dashboard.tailwind.css` already ship dark/light styling but no breakpoint-specific queue affordances exist besides a general `@media (max-width:900px)` font tweak.

## 3. Experience Goals

1. **Table on md+** (`min-width: 768px`): fully maintain the current column layout so operators keep their dense data view.
2. **Cards on mobile** (`max-width: 767px`): surface the same information stack in a tappable card with obvious entry points to job detail actions.
3. **Single source of truth**: queue field definitions live in one place so adding or reordering metadata updates both layouts.
4. **Zero behavioral regressions**: auto-refresh, filtering, telemetry summary, and navigation stay untouched.

Non-goals:

- Changing queue detail view controls.
- Altering API payload shapes (`toQueueRows`, `/api/queue/jobs` response) beyond read-only metadata extraction.
- Converting the dashboard to a framework (we remain vanilla JS + Tailwind-generated CSS).

## 4. Technical Design

### 4.1 Shared Row Definition

`toQueueRows(items)` already normalizes queue API payloads into `QueueRow` objects:

```ts
interface QueueRow {
  source: "queue" | "manifests";
  sourceLabel: string;
  id: string;
  payload: Record<string, unknown>;
  queueName: string;
  runtimeMode: string | null;
  skillId: string | null;
  rawStatus: string;
  title: string;
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
  link: string; // `/tasks/queue/:id`
}
```

We will introduce a static field map that both the table and the mobile card iterate over. Example shape:

```js
const queueFieldDefinitions = [
  { key: "queueName", label: "Queue", render: (row) => row.queueName || defaultQueueName },
  { key: "runtimeMode", label: "Runtime", render: (row) => renderRuntime(row.runtimeMode) },
  { key: "skillId", label: "Skill", render: (row) => escapeHtml(row.skillId || "-") },
  { key: "createdAt", label: "Created", render: (row) => formatTimestamp(row.createdAt) },
  { key: "startedAt", label: "Started", render: (row) => formatTimestamp(row.startedAt) },
  { key: "finishedAt", label: "Finished", render: (row) => formatTimestamp(row.finishedAt) },
];
```

- `renderRowsTable()` will iterate this definition for desktop and inject `<th>`/`<td>` pairs.
- The mobile card renderer will iterate the same array to create `<dt>/<dd>` pairs inside each card body.
- When new fields are needed (e.g., publish mode), they are added to `queueFieldDefinitions` once.

### 4.2 Responsive Layout Switching

Add a `renderQueueLayouts(rows)` helper that composes both layouts and lets CSS control visibility:

```js
function renderQueueLayouts(rows) {
  if (rows.length === 0) {
    return "<p class='small'>No rows available.</p>";
  }
  const tableHtml = renderQueueTable(rows);
  const cardsHtml = renderQueueCards(rows);
  return `
    <div class="queue-layouts">
      <div class="queue-table-wrapper" data-layout="table">${tableHtml}</div>
      <ul class="queue-card-list" data-layout="card" role="list">${cardsHtml}</ul>
    </div>
  `;
}
```

Integration points:

- `renderQueueListPage()` replaces `renderRowsTable(filteredRows)` with `renderQueueLayouts(filteredRows)` so filters affect both layouts simultaneously.
- `renderActivePage()` wraps `renderRowsTable(sortRows(rows))` in the same helper so the landing page gets responsive behavior for queue records. Orchestrator rows will continue to use the table view (cards show only the queue subset when a row’s `source === "queue"`).
- Manifest and orchestrator list pages may keep table-only layouts for now. The helper checks `row.source === "queue"` to avoid rendering cards for non-queue sources until we intentionally opt them in.

### 4.3 Card Composition

`renderQueueCards(rows)` renders each queue row as a card (non-queue rows return empty strings). Structure:

1. **Header row**: job title / ID link on the left, `statusBadge(row.source, row.rawStatus)` on the right.
2. **Meta line**: queue name + skill preview in muted text for quick scanning.
3. **Definition list**: iterate `queueFieldDefinitions` to show label/value pairs stacked on two columns for md- width and one column for very small widths.
4. **Actions**: include a `View details` button linking to `row.link` and an optional secondary action (e.g., `Retry`) only when the existing table row exposes that action (future work). For now the design keeps a single `View details` button to avoid duplicating controls.

Markup sketch (rendered via string templates to stay consistent with the rest of `dashboard.js`):

```html
<li class="queue-card">
  <div class="queue-card-header">
    <div>
      <a href="/tasks/queue/123" class="queue-card-title">Queue Job · 123</a>
      <p class="queue-card-meta">moonmind.jobs · auto/speckit-orchestrate</p>
    </div>
    <span class="status status-running">Running</span>
  </div>
  <dl class="queue-card-fields">
    <div>
      <dt>Runtime</dt>
      <dd>codex</dd>
    </div>
    <!-- etc, fed by queueFieldDefinitions -->
  </dl>
  <div class="queue-card-actions">
    <a href="/tasks/queue/123" class="button secondary full-width">View details</a>
  </div>
</li>
```

### 4.4 Table Composition

`renderRowsTable()` already outputs the correct `<table>` markup for desktop. We will:

- Rename the existing implementation to `renderQueueTable(rows)` (thin wrapper) and let `renderRowsTable` remain as the generic helper for other routes.
- Apply the shared field definitions so column order is derived from `queueFieldDefinitions`. This keeps the columns that operators expect (`Source`, `ID`, `Queue`, `Runtime`, `Skill`, `Status`, `Title`, `Created`, `Started`, `Finished`). Source/orchestrator rows continue to display inside the same table.
- Wrap the table markup with `<div class="queue-table-wrapper">` to allow CSS toggling.

### 4.5 CSS / Tailwind Rules

All styling updates land in `api_service/static/task_dashboard/dashboard.tailwind.css` and will be compiled into `dashboard.css` via the existing Tailwind/PostCSS pipeline. Key rules:

```css
.queue-layouts { display: grid; gap: 1rem; }
.queue-table-wrapper { display: none; }
.queue-card-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.75rem; }
.queue-card { border: 1px solid rgb(var(--mm-border)/0.8); border-radius: 1rem; padding: 1rem; background: rgb(var(--mm-panel)/0.78); box-shadow: var(--mm-shadow); }
.queue-card-header { display: flex; justify-content: space-between; gap: 0.75rem; align-items: flex-start; }
.queue-card-title { font-weight: 600; text-decoration: none; color: inherit; }
.queue-card-meta { margin: 0.2rem 0 0; color: rgb(var(--mm-muted)); font-size: 0.85rem; }
.queue-card-fields { margin: 1rem 0 0; display: grid; gap: 0.75rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.queue-card-fields dt { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.04em; color: rgb(var(--mm-muted)); }
.queue-card-fields dd { margin: 0.15rem 0 0; font-size: 0.92rem; font-weight: 500; }
.queue-card-actions { margin-top: 1rem; display: flex; gap: 0.5rem; }
.queue-card-actions .button { flex: 1 1 auto; text-align: center; }

@media (min-width: 768px) {
  .queue-table-wrapper { display: block; }
  .queue-card-list { display: none; }
}

@media (max-width: 767px) {
  .queue-table-wrapper { display: none; }
  .queue-card-list { display: grid; }
  .queue-card-fields { grid-template-columns: 1fr; }
}
```

Notes:

- Use tokenized colors (`--mm-*`) and avoid hard-coded values beyond alpha tweaks.
- `.button` is already defined globally; the new `.full-width` variant becomes `.queue-card-actions .button` using flex.

### 4.6 Accessibility & Performance

- Cards remain semantic list items with nested heading/definition list to aid screen readers.
- The `View details` button uses `<a role="button">` semantics so it works without JS hijacking.
- Duplicated markup is acceptable because the table and card versions are mutually hidden by CSS; the DOM size increase is < 2x for up to 200 rows and remains within acceptable limits. Polling continues to update a single HTML blob, so no extra network calls occur.

### 4.7 Extending queue fields (how-to)

1. **Add the field once**: append a new object inside `queueFieldDefinitions` in `api_service/static/task_dashboard/dashboard.js`. Pick `tableSection: "primary"` for queue/runtime metadata or `"timeline"` for timestamp columns. The `render(row)` function must return escaped HTML (reuse helpers like `renderRuntime`, `formatTimestamp`, or `escapeHtml`).
2. **Document intent inline**: keep the nearby comment accurate and mention any special formatting needs to help future readers.
3. **Rebuild both layouts automatically**: no other JS needs edits because `renderQueueCards` and `renderQueueTable` iterate the array automatically. The cards will emit a `<dt>/<dd>` pair for the new label, and the table will generate `<th>`/`<td>` cells with `data-field="<key>"`.
4. **Update tests**: extend `tests/task_dashboard/test_queue_layouts.js` (fixtures live under `tests/task_dashboard/__fixtures__/queue_rows.js`). Add assertions that `queueFieldDefinitions` includes the new key plus a regression that fails if cards or tables skip it.
5. **Validate + log**: rerun `./tools/test_unit.sh`, rebuild CSS via `npm run dashboard:css:min` (when available), and capture bundle delta + QA notes in `specs/037-queue-layout-switch/quickstart.md`. This keeps DOC-REQ-008 satisfied every time fields change.

## 5. Implementation Plan

1. **JS refactor (`api_service/static/task_dashboard/dashboard.js`)**
   - Extract `queueFieldDefinitions` near `toQueueRows()` so both layouts import the same column metadata.
   - Split `renderRowsTable` into `renderSharedRowsTable(rows)` (existing behavior) and `renderQueueTable(rows)` (queue-specific wrapper) if needed for clarity.
   - Implement `renderQueueCards(rows)` and `renderQueueLayouts(rows)` helpers described above.
   - Update `renderQueueListPage()` to call `renderQueueLayouts(filteredRows)`; ensure filter re-renders still call the helper.
   - Update `renderActivePage()` (and optionally manifest list) to wrap queue rows using the helper while keeping orchestrator/manifests table-only until they have card designs.
2. **CSS (`api_service/static/task_dashboard/dashboard.tailwind.css`)**
   - Add the `.queue-layouts`, `.queue-table-wrapper`, `.queue-card-*` styles using Tailwind directives (e.g., `@apply grid gap-3 md:block` etc.) so the generated CSS inherits tokens and dark-mode behavior.
   - Rebuild `dashboard.css` via the existing toolchain (already documented in `docs/TailwindStyleSystem.md`).
3. **Template (`api_service/templates/task_dashboard.html`)**
   - No markup changes needed; ensure the stylesheet link stays untouched.
4. **Testing**
   - Manual verification in responsive dev tools (320px, 768px, 1024px) to confirm the CSS switches correctly.
   - Run `./tools/test_unit.sh` to ensure no regressions.
   - Spot-check auto-refresh and filter interactions while toggling viewport widths.

## 6. Rollout Considerations

- Ship behind nothing: card layout is pure CSS/markup and safe to land without feature flags.
- Update `docs/TailwindStyleSystem.md` once implemented to mark "Mobile-specific nav/table refinements" as shipped.
- Monitor dashboard bundle size; string templates add <3 KB gzip so no action expected.

## 7. Future Work

- Extend card patterns to orchestrator/manifests/proposals once queue polish is validated.
- Add per-row action buttons (Cancel/Retry) into the card footer once the queue API exposes capability metadata without requiring the detail view.
