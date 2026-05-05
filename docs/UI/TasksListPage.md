# Tasks List Page

Status: Proposed desired-state contract  
Owners: MoonMind Engineering  
Last updated: 2026-05-04  
Canonical for: Mission Control tasks list route, execution-list controls, table sorting, column filters, filter URL state, and Google Sheets-like list filtering behavior

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs, or other local-only files. This document defines the product and UI contract for the page.

---

## 1. Purpose

This document defines the canonical design for the MoonMind **Tasks List** page.

The page helps operators inspect Temporal-backed MoonMind task executions in a task-oriented table. It must support fast scanning, stable pagination, clear status visibility, accessible sorting, and column-level filtering that can replace the current top-of-page filter dropdowns.

The desired column filtering model is intentionally similar to Google Sheets filters: each column header owns both sorting and a filter popover where users can search values, select or deselect values, include blanks, clear the column filter, and apply or cancel changes.

---

## 2. Related docs and implementation surfaces

Use this document for Tasks List page behavior.

Use related docs for system-level contracts:

- `docs/Api/ExecutionsApiContract.md` — `/api/executions` list contract, execution lifecycle fields, filters, count, and pagination semantics.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — Temporal Visibility and UI query model.
- `docs/UI/MissionControlArchitecture.md` — Mission Control shell and shared frontend architecture.
- `docs/UI/MissionControlDesignSystem.md` — shared Mission Control visual language.
- `docs/UI/CreatePage.md` — task authoring surface that creates many rows shown on this page.

Representative current implementation surfaces:

```text
api_service/api/routers/task_dashboard.py
api_service/api/routers/executions.py
frontend/src/entrypoints/tasks-list.tsx
frontend/src/entrypoints/tasks-list.test.tsx
frontend/src/styles/mission-control.css
```

---

## 3. Route and hosting model

The canonical Tasks List route is:

```text
/tasks/list
```

Rules:

1. `/tasks/list` is the canonical Tasks List route.
2. `/tasks` redirects to `/tasks/list`.
3. `/tasks/tasks-list` is a legacy alias and redirects to `/tasks/list`.
4. The page is server-hosted by FastAPI and rendered by the shared Mission Control React/Vite frontend.
5. The server renders the `tasks-list` page key into the boot payload.
6. The route uses a wide data-panel layout because the primary surface is a multi-column execution table.
7. Runtime dashboard configuration is generated server-side and passed through the boot payload.
8. The browser calls MoonMind APIs only. It must not call Temporal, GitHub, Jira, object storage, or runtime providers directly.

---

## 4. Product stance

The Tasks List page is an operator scanning surface, not a generic Temporal namespace browser.

Core rules:

1. The default view is task-oriented and shows ordinary user-created task executions.
2. The normal Tasks List page is not a workflow-kind browser. It does not need a `Kind` column.
3. System workflows are hidden from ordinary Tasks List users. Provider-profile managers, internal monitors, maintenance workflows, and other platform-owned executions belong in an admin diagnostics surface instead of the main task table.
4. Manifest-ingest workflows belong on the Manifests page or a future user-workflow diagnostics view. They should not force a `Kind`, `Workflow Type`, or `Entry` column into the default task list.
5. Sorting and filtering are table behaviors and should be expressed on the relevant task columns instead of as detached dropdowns above the table.
6. The page should make the active query obvious through column filter indicators and active filter chips.
7. URL state must be shareable and reloadable.
8. Pagination, sorting, and filtering must be deterministic across refreshes.
9. Live updates are valuable, but they must not corrupt an in-progress filter selection.

---

## 5. Current page behavior

This section describes the current Tasks List page before the proposed column-filter redesign.

### 5.1 Page shell

The current page renders a single vertical stack with two major surfaces:

1. a **control deck** at the top;
2. a **data slab** containing results, pagination, desktop table, and mobile cards.

The control deck contains:

- page title: `Tasks List`;
- `Live updates` checkbox;
- polling status copy;
- disabled-state notice when the Temporal list feature is disabled;
- a filter form;
- active filter chips;
- `Clear filters` action.

The page reads `dashboardConfig.pollIntervalsMs.list` from the boot payload and falls back to a five-second polling interval. The list is disabled when `dashboardConfig.features.temporalDashboard.listEnabled === false`.

### 5.2 Current top filters

The current filter form exposes these controls:

| Control | Current behavior |
| --- | --- |
| Scope | Selects `tasks`, `user`, `system`, or `all`. Defaults to `tasks` unless an explicit workflow type or entry query is present. |
| Workflow Type | Disabled while `scope = tasks`; otherwise offers user, system, or all workflow types according to the selected scope. |
| Status | Filters by canonical MoonMind lifecycle state. |
| Entry | Filters by `run` or `manifest`. |
| Repository | Text input for an `owner/repo` value. Trimmed value is used for the request and query key. |

Current workflow type groups:

| Group | Workflow types |
| --- | --- |
| User workflows | `MoonMind.Run`, `MoonMind.ManifestIngest` |
| System workflows | `MoonMind.ProviderProfileManager` |
| All workflows | User workflows plus system workflows |

Current canonical status options:

```text
scheduled
initializing
waiting_on_dependencies
planning
awaiting_slot
executing
proposals
awaiting_external
finalizing
completed
failed
canceled
```

Current entry options:

```text
run
manifest
```

### 5.3 Current URL state

The current page stores list state in the URL query string.

Current URL parameters:

| URL parameter | Meaning |
| --- | --- |
| `scope` | Current list scope when non-default or when needed to preserve entry/workflow filters. |
| `workflowType` | Selected workflow type. |
| `state` | Selected lifecycle state. |
| `entry` | Selected entry value. |
| `repo` | Repository filter. |
| `limit` | Page size. |
| `nextPageToken` | Current opaque pagination cursor. |
| `sort` | Selected table sort field when non-default. |
| `sortDir` | `asc` or `desc` when non-default. |

Rules:

1. Query-string state is synchronized with `history.replaceState`.
2. Filter changes reset pagination to the first page.
3. Page-size changes reset pagination to the first page.
4. Sort state is persisted in the URL.
5. The current list request does not send sort parameters to the API; rows are sorted in the browser after the current page is fetched.

### 5.4 Current API request

The current page queries:

```text
GET /api/executions?source=temporal&pageSize=<pageSize>&scope=<scope>
```

Optional query parameters are appended when active:

```text
nextPageToken
workflowType
state
entry
repo
```

The response is parsed as an execution list response with:

| Field | Meaning |
| --- | --- |
| `items` | Current page of rows. |
| `nextPageToken` | Opaque cursor for the next page. |
| `count` | Current filtered count when available. |
| `countMode` | Count confidence, such as `exact`. |

### 5.5 Current row model

The current table row model includes these display fields:

| Field | Meaning |
| --- | --- |
| `taskId` | Task-oriented execution identifier; linked to detail view. |
| `source` | Execution source, currently Temporal for this page. |
| `workflowType` | Root workflow type when returned. |
| `repository` | Repository display value, if available. |
| `integration` | Integration display value, if available. |
| `targetRuntime` | Runtime identifier, displayed through a human-readable runtime formatter. |
| `targetSkill` | Primary skill identifier, when available. |
| `taskSkills` | Skill list, when available. |
| `title` | Display title. |
| `status` | Coarse dashboard status. |
| `state` | MoonMind lifecycle state. |
| `rawState` | Raw lifecycle state preferred for status pill display. |
| `temporalStatus` | Simplified Temporal status when available. |
| `scheduledFor` | Scheduled timestamp. |
| `createdAt` | Created timestamp. |
| `closedAt` | Finished timestamp. |
| `entry` | Entry kind, such as `run` or `manifest`. |
| `dependsOn` | Dependency identifiers. |
| `blockedOnDependencies` | Whether dependency blocking should be summarized. |

### 5.6 Current desktop table

The current desktop table columns are:

| Order | Column | Sort field |
| --- | --- | --- |
| 1 | ID | `taskId` |
| 2 | Runtime | `targetRuntime` |
| 3 | Skill | `targetSkill` |
| 4 | Repository | `repository` |
| 5 | Status | `status` |
| 6 | Title | `title` |
| 7 | Scheduled | `scheduledFor` |
| 8 | Created | `createdAt` |
| 9 | Finished | `closedAt` |

Rules:

1. Header buttons sort the current page.
2. The default sort is `scheduledFor` descending.
3. Timestamp columns sort by parsed timestamp. `scheduledFor` falls back to `createdAt` when no scheduled timestamp exists.
4. Status sorting uses `rawState` or `state` when available.
5. Non-timestamp columns sort as lowercase strings.
6. Ties sort by `taskId` descending.
7. Each status cell renders an `ExecutionStatusPill` using `rawState || state || status`.
8. The `Started` timestamp is intentionally not shown in the list presentation.
9. Rows blocked on dependencies show a compact dependency summary under the title.

### 5.7 Current mobile cards

The current mobile layout renders a card list with:

- title link;
- task ID;
- runtime, skill, and workflow type metadata;
- status pill;
- field grid for ID, Runtime, Skill, Repository, Scheduled, Created, and Finished;
- dependency summary when applicable;
- full-width `View details` card action.

### 5.8 Current empty, loading, error, and pagination states

Rules:

1. Loading state shows `Loading tasks...`.
2. API errors render a visible error notice.
3. Empty first pages show `No tasks found for the current filters.`
4. Empty later pages keep the previous-page button enabled.
5. Pagination uses an opaque `nextPageToken` plus a client-side cursor stack for previous-page navigation.
6. The results toolbar shows `Page N`, row range, and count when available.
7. Page size is controlled through the shared `PageSizeSelector`.

---

## 6. Desired page layout after column filters

The desired state removes the current top filter form once equivalent column filters are available.

The page should render these surfaces:

| Surface | Desired contents |
| --- | --- |
| Header control deck | Page title, Live updates toggle, polling copy, feature-disabled notice. |
| Active query row | Active filter chips, clear-all action, optional saved-view chip. No Scope, Workflow Type, Status, Entry, or Repository dropdown/input controls. |
| Results toolbar | Page summary, page size, pagination, optional column visibility control. |
| Desktop table | Sortable and filterable column headers. |
| Mobile filter sheet | Same column filters as the table, adapted for cards. |
| Mobile cards | Existing card presentation, with filter affordances available through the mobile filter sheet. |

Rules:

1. The top filter dropdowns are replaced by column header filters.
2. Repository filtering moves from the top text input to the Repository column filter.
3. Status filtering moves from the top Status dropdown to the Status column filter.
4. Runtime filtering is added to the Runtime column filter.
5. Scope, workflow type, and entry controls are removed from the normal Tasks List page instead of being represented by a `Kind` column.
6. The normal page always queries the user-visible task scope; system workflows are not available from the ordinary task table.
7. Active filters are still summarized in a row of chips so users do not have to inspect every header.
8. Filter chips must be clickable and reopen the corresponding column filter.
9. `Clear filters` remains available when any filter is active.
10. Live updates remain in the header because they are page behavior, not table filtering.
11. Page size and pagination remain in the results toolbar because they are result-window controls, not filters.

---

## 7. Column set in the desired design

The desired desktop table uses this default column model:

| Default visibility | Column | Primary field | Sort | Filter |
| --- | --- | --- | --- | --- |
| Visible | ID | `taskId` | Yes | Text contains / exact IDs |
| Visible | Runtime | `targetRuntime` | Yes | Value checklist, blanks |
| Visible | Skill | `targetSkill` plus `taskSkills` | Yes | Value checklist, blanks |
| Visible | Repository | `repository` | Yes | Value checklist, text search, blanks |
| Visible | Status | `rawState || state || status` | Yes | Canonical status checklist |
| Visible | Title | `title` | Yes | Text contains |
| Visible | Scheduled | `scheduledFor` | Yes | Date range, relative dates, blanks |
| Visible | Created | `createdAt` | Yes | Date range, relative dates |
| Visible | Finished | `closedAt` | Yes | Date range, relative dates, blanks |
| Optional | Integration | `integration` | Yes | Value checklist, blanks |

Rules:

1. The normal Tasks List table does not include a `Kind` column.
2. The normal table does not include `Workflow Type` or `Entry` columns by default.
3. The default query is the task-run list. In current API terms, this is equivalent to `scope=tasks`, which is `WorkflowType=MoonMind.Run` and `mm_entry=run`.
4. System workflow rows must not appear in the normal task table, even through column filters or old URL parameters.
5. Manifest ingest rows should stay on the Manifests page unless a separate user-workflow diagnostics view is explicitly designed.
6. The table may hide optional columns by default to preserve width, but optional columns must not reintroduce ordinary access to system workflow browsing.
7. The mobile filter sheet must expose the same filterable task columns as the desktop table.
8. The table must not expose raw Temporal Visibility query syntax to ordinary users.

### 7.1 Admin diagnostics escape hatch

System and all-workflow browsing is useful for debugging, but it is not part of the normal Tasks List UX.

Rules:

1. System workflows belong in an admin diagnostics surface such as Settings -> Operations -> Workflow Diagnostics, `/tasks/diagnostics`, or a similarly explicit route.
2. A diagnostics surface may expose workflow type, entry, owner, namespace, run ID, raw Temporal status, and system workflow filters because its purpose is platform debugging.
3. Diagnostics access must be permission-gated. Ordinary users cannot widen `/tasks/list` into system workflow visibility by editing URL parameters.
4. If compatibility routes or query parameters such as `scope=system` are still accepted, the normal Tasks List page must either ignore them safely, redirect authorized admins to diagnostics, or show a recoverable message explaining that system workflows moved to diagnostics.
5. The product contract for `/tasks/list` remains task-oriented even if the underlying `/api/executions` endpoint can list broader workflow scopes.

---

## 8. Column header interaction model

Each sortable/filterable header is a compound control with two distinct targets:

1. **Sort button** — the column label area.
2. **Filter button** — a funnel icon area.

Example conceptual header:

```text
Runtime ▲  [filter icon]
```

Rules:

1. Activating the label toggles sorting for that column.
2. Activating the filter icon opens the column filter popover.
3. Sorting must not clear filters.
4. Filtering must not clear the current sort unless the sorted column becomes unavailable.
5. A sorted column shows `▲` for ascending or `▼` for descending.
6. A filtered column shows an active filter icon, visually distinct from an unfiltered icon.
7. A column with both sort and filter active shows both indicators.
8. Header controls must preserve the existing `aria-sort` behavior for the sort target.
9. The filter target must expose accessible state such as `Filter Runtime. No filter applied.` or `Filter Status. Filter active: excluding canceled.`

### 8.1 Sort behavior

The initial desired sort remains:

```text
sort = scheduledFor
sortDir = desc
```

Rules:

1. Clicking an unsorted timestamp column defaults to descending.
2. Clicking an unsorted non-timestamp column defaults to ascending.
3. Clicking the currently sorted column toggles ascending and descending.
4. Only one primary sort is required for this page.
5. Multi-column sorting is out of scope for the first version.
6. Desired long-term behavior is server-authoritative sorting so sort order remains stable across pages.
7. While the API does not support server-side sort for every column, the UI may keep client-side current-page sorting as a compatibility behavior, but it must not present current-page sorting as a global result ordering guarantee.

---

## 9. Filter popover design

The filter popover is an anchored, keyboard-accessible panel. It is not a native `select` dropdown.

### 9.1 Common popover structure

Each value-list filter popover contains:

1. column title;
2. current filter summary;
3. sort commands;
4. value search input;
5. `Select all` checkbox;
6. scrollable value checklist;
7. blank-value row when the field can be blank;
8. `Clear` action;
9. `Cancel` action;
10. `Apply` action.

Representative layout:

```text
Runtime
All values selected

Sort A to Z
Sort Z to A

Search values...
[✓] Select all
[✓] Codex CLI (18)
[✓] Claude Code (11)
[✓] Local Agent (3)
[✓] Blanks (2)

Clear     Cancel     Apply
```

Rules:

1. Checkbox changes are staged locally until `Apply` is activated.
2. `Cancel`, `Escape`, or outside click closes the popover without applying staged changes.
3. `Clear` removes the filter for that column and applies immediately or stages a clear state that `Apply` confirms; the chosen behavior must be consistent across columns.
4. Search filters the available checklist values inside the popover; it does not filter table rows until the user applies a value selection or text filter.
5. Value rows show display labels and counts when count data is available.
6. The popover must support an `Only` quick action on value hover/focus for power users, but the row checkbox remains the primary interaction.
7. The menu should show status pills for Status values and human-readable runtime labels for Runtime values.
8. Long value lists must be virtualized or paginated.
9. Value labels must never render untrusted HTML.

### 9.2 Status filter popover

The Status filter is a value-list popover backed by canonical lifecycle states.

Rules:

1. The checklist uses the canonical status order from the current page contract.
2. Each row shows the same display text and pill styling used in table rows where possible.
3. Filtering by Status maps to the MoonMind lifecycle state, not the coarse dashboard status.
4. The filter must tolerate historical rows where `rawState`, `state`, and `status` differ by using the same display precedence as the table: `rawState || state || status`.

### 9.3 Runtime filter popover

The Runtime filter is a value-list popover backed by runtime identifiers.

Rules:

1. The checklist stores raw runtime identifiers such as `codex_cli`.
2. The checklist displays human-readable labels such as `Codex CLI`.
3. Blanks are represented as `—` and can be included or excluded.
4. Runtime filtering must be available even though the current top filter form does not expose runtime filtering.

### 9.4 Repository filter popover

The Repository filter combines value selection and text filtering.

Rules:

1. The default mode is value checklist for repositories present in the filtered result universe.
2. The popover search input filters repository values in the checklist.
3. A secondary text mode may support `contains` or exact text when a repository is not present in the loaded facet list.
4. Existing top-level repository text input behavior maps to an exact Repository column filter.
5. Blanks can be included or excluded.

### 9.5 Date filter popovers

Scheduled, Created, and Finished use date filter popovers.

Required controls:

| Control | Purpose |
| --- | --- |
| Sort newest first | Sets descending sort. |
| Sort oldest first | Sets ascending sort. |
| From | Inclusive lower bound. |
| To | Inclusive upper bound. |
| Relative presets | Optional shortcuts such as today, last 24 hours, last 7 days. |
| Blanks | Available for Scheduled and Finished. |

Rules:

1. Dates use local display formatting, but URL/API filter values use ISO 8601 timestamps or date-only values with documented inclusive behavior.
2. `Finished` blanks represent active or unfinished work.
3. `Scheduled` blanks represent unscheduled immediate work.

### 9.6 Text filter popovers

ID and Title use text-oriented popovers.

Rules:

1. ID supports exact ID matching and contains matching.
2. Title supports contains matching.
3. Text filtering should be debounced for preview counts if preview counts are shown.
4. Applying a text filter resets pagination to the first page.
5. Text filters must trim leading/trailing whitespace for query behavior while preserving the user-entered text in the input until apply/cancel.

---

## 10. Selection semantics

Column filters use AND semantics across columns and OR semantics within a column.

Examples:

| User selection | Meaning |
| --- | --- |
| Status = `executing` and `planning` | Show rows whose state is executing OR planning. |
| Runtime = `codex_cli`; Status = `failed` | Show rows whose runtime is Codex CLI AND state is failed. |
| Repository excludes `owner/archived` | Show all repositories except `owner/archived`, subject to other filters. |

### 10.1 Include mode and exclude mode

The UI must distinguish two cases:

| Mode | How the user gets there | Desired URL/API meaning |
| --- | --- | --- |
| Include mode | User selects a subset from none, uses `Only`, or unchecks many values until a small positive set remains. | Include only the selected values. |
| Exclude mode | User starts from all values and deselects one or more unwanted values. | Include all current and future values except the deselected values. |

Rules:

1. Deselecting one status such as `canceled` from an otherwise-all checklist should create an exclude filter: `not canceled`.
2. Exclude mode allows new values that arrive through live updates to appear automatically.
3. Include mode hides new values until the user explicitly selects them.
4. `Select all` clears the column filter.
5. Deselecting all values is allowed only when the user confirms that the result will be empty, or the UI prevents the impossible state and suggests clearing the filter instead.

---

## 11. Active filter chips

Active filters are summarized above the results table.

Representative chips:

```text
Status: not canceled
Runtime: Codex CLI +1
Repository: MoonLadderStudios/MoonMind
Finished: blank
```

Rules:

1. Every active column filter has a chip.
2. Clicking a chip opens the corresponding column filter popover.
3. Each chip has a remove action that clears only that column filter.
4. `Clear filters` clears every column filter and restores the default task-run view.
5. Chips use product labels, not raw API parameter names.
6. Chips must remain visible on mobile through a horizontally scrollable row or compact filter summary button.

---

## 12. URL state in the desired design

The URL remains the shareable source of client-visible query state.

Required URL state:

| URL state | Purpose |
| --- | --- |
| `limit` | Page size. |
| `nextPageToken` | Current pagination cursor. |
| `sort` | Current sort field. |
| `sortDir` | `asc` or `desc`. |
| column filter params | Active column filters. |

### 12.1 Backward compatibility

Existing URLs must continue to fail safe:

| Existing parameter | Desired mapping |
| --- | --- |
| `scope=tasks` | Default task-run view. No visible column filter is required. |
| `scope=user` | Prefer the default task-run view on `/tasks/list`; manifest ingest belongs on the Manifests page or diagnostics. |
| `scope=system` | Not honored by the normal Tasks List page. Authorized admins may be redirected to diagnostics; ordinary users stay in the default task-run view or see a recoverable message. |
| `scope=all` | Not honored by the normal Tasks List page. Authorized admins may be redirected to diagnostics; ordinary users stay in the default task-run view or see a recoverable message. |
| `workflowType=MoonMind.Run` | Default task-run view when paired with `entry=run` or no entry. |
| `workflowType=MoonMind.ManifestIngest` | Redirect to the Manifests page or show a recoverable message; do not add a `Workflow Type` column to the task table. |
| `workflowType=<system value>` | Not honored by the normal Tasks List page; use admin diagnostics when authorized. |
| `state=<value>` | Status column include filter for one value. |
| `entry=run` | Default task-run view. No visible column filter is required. |
| `entry=manifest` | Redirect to the Manifests page or show a recoverable message. |
| `repo=<value>` | Repository exact include filter for one value. |

Rules:

1. Existing query parameters remain accepted on load so old shared links do not break.
2. Compatibility handling must never reveal system workflows in the ordinary Tasks List page.
3. After the user changes filters in the new UI, the URL should rewrite to the new canonical task-column filter encoding.
4. Shared old links should either preserve meaning inside the task-focused page, redirect to the more appropriate page, or explain why the old workflow scope moved.
5. Filter changes reset `nextPageToken` and the previous-page cursor stack.

### 12.2 Canonical filter encoding

The desired API and URL should support multi-value include and exclude filters.

Recommended URL shape:

```text
/tasks/list?stateNotIn=canceled&targetRuntimeIn=codex_cli,claude_code&limit=50&sort=scheduledFor&sortDir=desc
```

Recommended parameters:

| Parameter | Meaning |
| --- | --- |
| `stateIn` / `stateNotIn` | Canonical lifecycle state values. |
| `targetRuntimeIn` / `targetRuntimeNotIn` | Runtime identifiers. |
| `targetSkillIn` / `targetSkillNotIn` | Skill identifiers. |
| `repoIn` / `repoNotIn` | Exact repository values. |
| `repoContains` | Repository text contains search. |
| `integrationIn` / `integrationNotIn` | Integration values. |
| `taskId` / `taskIdContains` | ID exact or contains filter. |
| `titleContains` | Title text filter. |
| `scheduledFrom` / `scheduledTo` | Scheduled timestamp bounds. |
| `createdFrom` / `createdTo` | Created timestamp bounds. |
| `closedFrom` / `closedTo` | Finished timestamp bounds. |
| `<field>Blank` | Include blanks for fields where blank is meaningful. |

Rules:

1. Values in comma-separated lists must be URL-encoded.
2. If a value can contain commas in the future, the client and API must support repeated parameters as an equivalent representation.
3. The API must reject contradictory include and exclude filters on the same field with a clear validation error.
4. The browser must normalize empty lists away rather than sending no-op filters.

---

## 13. API and data requirements for column filtering

The current `/api/executions` list endpoint supports a limited set of exact filters. The desired column filter experience needs broader list and facet support.

### 13.1 List query requirements

The list endpoint should support:

1. server-authoritative sort field and direction;
2. multi-value include filters;
3. multi-value exclude filters;
4. text filters for ID, title, and repository;
5. date range filters;
6. blank/null filters where meaningful;
7. deterministic pagination under active filters;
8. count and count mode for the fully filtered result.

Rules:

1. Filters across columns use AND semantics.
2. Multiple values within one column use OR semantics.
3. Exclude filters are evaluated after field normalization.
4. Display labels are never sent as canonical filter values when raw values exist.
5. The API remains the authority for access control; users cannot widen their visibility through filter params.
6. The normal Tasks List query is always bounded to user-visible task executions. Backend list support for broader workflow scopes must not leak into this page.

### 13.2 Facet query requirements

The UI needs facet data so a filter popover can show values and counts beyond the current page.

Recommended endpoint:

```text
GET /api/executions/facets?source=temporal&facet=targetRuntime&<current filters except targetRuntime>
```

Representative response:

```json
{
  "facet": "targetRuntime",
  "items": [
    { "value": "codex_cli", "label": "Codex CLI", "count": 18 },
    { "value": "claude_code", "label": "Claude Code", "count": 11 }
  ],
  "blankCount": 2,
  "countMode": "exact",
  "truncated": false,
  "nextPageToken": null
}
```

Rules:

1. Facets are scoped by the current user and authorization model.
2. Facet requests include all active filters except the filter for the facet being opened, unless the user asks to search within the currently selected subset.
3. Static facets such as Status may come from the frontend enum plus server counts.
4. Dynamic facets such as Runtime, Skill, Repository, and Integration should come from server data.
5. Facet counts must reflect the current query context.
6. If exact counts are expensive, the response may set `countMode` to an estimated or unknown mode; the UI must label those counts accordingly or omit counts.
7. Large facets may be paginated and searched server-side.
8. Facet failure must not break the table; the UI can fall back to values in the currently loaded page with a visible “current page values only” notice.
9. Facet results must not include system-only workflow values or counts on the normal Tasks List page.

---

## 14. Live updates and filter stability

Live updates continue to be useful after column filters, but they must not disrupt staged menu choices.

Rules:

1. When no filter popover is open, live updates may refetch according to the configured polling interval.
2. When a filter popover is open, the checklist snapshot should remain stable until the popover closes or the user explicitly refreshes values.
3. New rows that match active filters may appear after polling.
4. Rows that no longer match active filters may disappear after polling.
5. Include-mode filters do not automatically include newly discovered values.
6. Exclude-mode filters do include newly discovered values unless the value is excluded.
7. The page may show subtle copy such as `Values updated after you opened this filter` when a facet changes while a popover is open.
8. Live updates must not overwrite staged, unapplied filter changes.

---

## 15. Accessibility requirements

Rules:

1. Sort and filter controls must be separately reachable by keyboard.
2. Header sort state must continue using `aria-sort` on the `th` or equivalent table-header element.
3. Filter buttons must expose `aria-haspopup`, `aria-expanded`, and a descriptive accessible name.
4. Filter popovers with checkboxes and search should use dialog or popover semantics rather than pretending to be a simple menu.
5. Focus moves into the popover when it opens and returns to the originating filter button when it closes.
6. `Escape` cancels staged changes and closes the popover.
7. `Enter` on `Apply` applies staged changes and closes the popover.
8. Checkbox labels include value label and count when shown.
9. Active filter chips expose remove buttons with names such as `Remove Status filter`.
10. Mobile filter sheet controls must be equivalent to desktop controls for screen-reader and keyboard users.
11. Color must not be the only indicator of active sort, active filter, or selected status.

---

## 16. Mobile behavior

The mobile card layout remains the primary narrow-screen presentation. Column header filters are adapted into a mobile filter sheet.

Rules:

1. The mobile results toolbar includes a `Filters` button when the table header is not visible.
2. The mobile filter sheet lists the same filterable columns as desktop.
3. Each column row opens the same filter editor used by desktop, adapted to full-screen or bottom-sheet layout.
4. Active filters appear as chips above the cards.
5. Card fields may expose small contextual filter actions, such as filtering to a visible runtime or status, but those actions are optional.
6. Mobile filter changes reset pagination to the first page just like desktop.
7. Mobile users must not need the removed top dropdowns to reach status, runtime, skill, repository, title, ID, or date filters.
8. Mobile users cannot reveal system workflows from the ordinary task-card view; diagnostics remains a separate admin surface.

---

## 17. Empty and error states after column filters

Rules:

1. If no rows match active filters on the first page, show `No tasks found for the current filters.`
2. The empty state should include `Clear filters` when filters are active.
3. If a specific filter is likely too narrow, the empty state may summarize active chips but should not guess which filter is wrong.
4. If a facet request fails, show an inline warning inside the popover and allow retry.
5. If the list request rejects a filter parameter, show the API error and preserve the user's filter state so it can be edited.
6. If an old URL contains unsupported filter combinations, the page should either map it to a valid task-list state, redirect to an appropriate page, or show a recoverable error with `Clear filters`.

---

## 18. Security and privacy

Rules:

1. The browser calls only MoonMind APIs.
2. Facet values are subject to the same authorization and owner scoping as list rows.
3. Hidden or unauthorized values must not appear in facet lists or counts.
4. Filter params must not bypass backend authorization.
5. URL filter state must not include secrets.
6. Repository, title, skill, runtime, status, and integration labels are rendered as text, never trusted HTML.
7. The API should bound text filter lengths and value-list sizes.
8. Invalid filter values should return structured validation errors rather than raw query failures.
9. System workflow visibility is a backend authorization decision and is not exposed by `/tasks/list` column filters.

---

## 19. Testing contract

The implementation should add or preserve tests for the following behaviors:

1. `/tasks/list` renders the Tasks List page with one control deck and one data slab.
2. Existing `state` and `repo` URLs load into equivalent column filters.
3. Existing `scope`, `workflowType`, and `entry` URLs fail safe: ordinary users are kept in the task-run view, routed to the appropriate page, or shown a recoverable message without revealing system workflows.
4. The top Scope, Workflow Type, Status, Entry, and Repository controls are absent once column filters are enabled.
5. The default desktop table has no `Kind`, `Workflow Type`, or `Entry` column.
6. Header label activation toggles sort without opening the filter popover.
7. Header filter icon activation opens the correct filter popover without changing sort.
8. Sorted headers retain correct `aria-sort` values.
9. Filtered headers expose active filter state in their accessible names.
10. Status filter shows canonical lifecycle states in canonical order.
11. Runtime filter displays human-readable labels while applying raw runtime identifiers.
12. Deselecting `canceled` from an all-selected Status checklist applies an exclude filter and creates a chip such as `Status: not canceled`.
13. Selecting only `Codex CLI` and `Claude Code` applies a Runtime include filter.
14. Applying any filter resets `nextPageToken` and the previous-page cursor stack.
15. `Clear filters` clears all column filters and returns to the default task-run view.
16. Filter chips can clear individual filters.
17. Old single-value query params still round-trip to active filters.
18. Mobile filter sheet exposes the same filterable task columns as desktop.
19. System/all workflow scopes remain absent from the normal Tasks List and are available only through an admin diagnostics surface when authorized.
20. Facet failure falls back gracefully or shows a recoverable inline error.
21. Live polling does not mutate staged filter popover selections.
22. Empty later pages keep previous-page navigation available.
23. The `Started` timestamp remains absent from the list presentation unless a separate design explicitly reintroduces it.

---

## 20. Non-goals

The column filter design does not require:

1. spreadsheet-style cell editing;
2. arbitrary pivot tables;
3. multi-column sort in the first version;
4. user-authored raw Temporal Visibility SQL;
5. direct browser calls to Temporal;
6. saving named filter views in the first version;
7. replacing page size or pagination controls;
8. removing the Live updates toggle;
9. exposing system workflow browsing through the normal Tasks List page.

---

## 21. Desired implementation sequence

This section describes safe product sequencing without making this document a rollout checklist.

Recommended order:

1. Preserve the current page behavior and URL compatibility.
2. Add the reusable column header sort/filter component behind a feature flag.
3. Add column filter state, chips, and URL mapping while the old top filters still exist.
4. Add API support for multi-value task filters and facets.
5. Move Status and Repository behavior into column filters.
6. Remove ordinary Scope, Workflow Type, and Entry controls from `/tasks/list`; keep a compatibility parser that fails safe and sends system/all workflow browsing to admin diagnostics when authorized.
7. Add Runtime and Skill column filters.
8. Remove the old top filter controls after parity tests pass.
9. Keep old URL parameter parsing indefinitely or until a documented compatibility window ends.

Final desired state: the Tasks List page feels like a compact, operational spreadsheet for user-visible task executions, where each task column owns its own sort and filter behavior and the page no longer needs detached filter dropdowns above the table.
