# Workflows List Page

Status: Living product and implementation contract  
Owners: MoonMind Engineering  
Last updated: 2026-06-28  
Canonical for: dashboard Workflows list route, execution-list controls, table sorting, column filters, filter URL state, Google Sheets-like list filtering behavior, and Progress column sort/filter semantics

**Implementation tracking:** Rollout and backlog notes live under `docs/tmp/` or in gitignored local-only handoffs. This document defines the product and UI contract for the page.

---

## 1. Purpose

This document defines the canonical design for the MoonMind **Workflows List** page.

The page helps operators inspect Temporal-backed MoonMind Workflow Executions in a Workflow-oriented table. It must support fast scanning, stable pagination, clear status visibility, accessible sorting, shareable filters, and column-level filtering that works consistently across desktop table headers and the mobile filter sheet.

The column filtering model is intentionally similar to Google Sheets filters: each filterable column owns a filter control where users can stage changes, search or enter values, include or exclude values when appropriate, include or exclude blanks when meaningful, clear a column filter, cancel staged edits, and apply the filter.

---

## 2. Related docs and implementation surfaces

Use this document for Workflows List page behavior.

Use related docs for system-level contracts:

- `docs/Api/ExecutionsApiContract.md` — `/api/executions` list contract, execution lifecycle fields, filters, count, and pagination semantics.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — Temporal Visibility and UI query model.
- `docs/Temporal/StepLedgerAndProgressModel.md` — workflow-owned step ledger and bounded execution progress summary.
- `docs/UI/WorkflowConsoleArchitecture.md` — workflow console shell and shared frontend architecture.
- `docs/UI/DashboardDesignSystem.md` — shared dashboard visual language.
- `docs/UI/WorkflowWorkspaceSidebar.md` — desktop list-to-detail workspace transitions, sidebar list context, and mobile card-to-detail behavior.
- `docs/UI/CreatePage.md` — Workflow authoring surface that creates many rows shown on this page.

Representative implementation surfaces:

```text
api_service/api/routers/workflow_console.py
api_service/api/routers/executions.py
frontend/src/entrypoints/workflow-list.tsx
frontend/src/entrypoints/workflow-list.test.tsx
frontend/src/styles/dashboard.css
```

---

## 3. Route and hosting model

The canonical Workflows List route is:

```text
/workflows
```

Rules:

1. `/workflows` is the canonical Workflows List route.
2. Legacy `/tasks/*` list routes are removed from the route table without redirects unless an explicit compatibility route is introduced.
3. The page is server-hosted by FastAPI and rendered by the shared dashboard React/Vite frontend.
4. The server renders the `workflow-list` page key into the boot payload.
5. The route uses a wide data-panel layout because the primary surface is a multi-column execution table.
6. Runtime dashboard configuration is generated server-side and passed through the boot payload.
7. The browser calls MoonMind APIs only. It must not call Temporal, GitHub, Jira, object storage, or runtime providers directly.

---

## 4. Product stance

The Workflows List page is an operator scanning surface, not a generic Temporal namespace browser.

Core rules:

1. The default view is Workflow-oriented and shows ordinary user-created Workflow Executions.
2. The normal Workflows List page is not a workflow-kind browser. It does not need a `Kind` column.
3. System workflows are hidden from ordinary Workflows List users. Provider-profile managers, internal monitors, maintenance workflows, and other platform-owned executions belong in an admin diagnostics surface instead of the main Workflow table.
4. Manifest-ingest workflows belong on the Manifests page or a future user-workflow diagnostics view. They should not force a `Kind`, `Workflow Type`, or `Entry` column into the default Workflows list.
5. Sorting and filtering are table behaviors and should be expressed on the relevant Workflow columns instead of as detached dropdowns above the table.
6. The page should make the active query obvious through column filter indicators, active filter chips, and URL state where that state is server-authoritative.
7. Pagination, sorting, and filtering must be deterministic across refreshes once server-authoritative sorting is enabled.
8. Live updates are valuable, but they must not corrupt an in-progress filter selection.

---

## 5. Current implementation snapshot

This section describes the Workflows List implementation as of this document update.

### 5.1 Page shell

The page renders a single vertical stack with:

1. optional notices for disabled list configuration, ignored workflow-scope params, validation errors, and cursor reset recovery;
2. a data slab containing filter access, view options, desktop table, mobile cards, pagination, page size, and live-update status.

The page reads `dashboardConfig.pollIntervalsMs.list` from the boot payload and falls back to a five-second polling interval. The list is disabled when `dashboardConfig.features.temporalDashboard.listEnabled === false`.

### 5.2 Filter access

The old top-of-page filter form is no longer the product model for the normal Workflows List page.

Current UI behavior:

| Surface | Behavior |
| --- | --- |
| Desktop table headers | Filter buttons are available on table-visible filterable columns. |
| Advanced filters drawer | Exposes the full filter set, including fields not currently visible in the desktop table. |
| Mobile filter sheet | Uses the advanced filter drawer pattern because table headers are not visible. |
| Active filter chips | Summarize applied filters and reopen the corresponding filter section. |
| View options | Controls density, column visibility, and live-update preference. |

Current desktop header filters exist for:

```text
Workflow
Status
Repository
Runtime
Updated
```

Current advanced drawer filters exist for:

```text
ID
Title
Status
Repository
Runtime
Skill
Updated
Scheduled
Created
Finished
```

The **Progress** column is currently display-only. This document defines the design update required to make Progress sortable and filterable in the same family as the other columns.

### 5.3 Current URL and request state

The current page stores applied filters, page size, and pagination cursor in the URL query string.

Current URL behavior:

1. Filter changes reset pagination to the first page.
2. Page-size changes reset pagination to the first page.
3. `nextPageToken` is removed when filters or page size change.
4. Current frontend table sorting is client-side and current-page-only.
5. Current frontend sorting is intentionally not seeded from the URL, written to the URL, or sent to the list API. This avoids implying a global server-side order across the full filtered result set before the UI adopts server-authoritative sort for every visible sortable column.
6. The UI labels current sorting scope through footer copy, header tooltip text, and screen-reader hints.

Current API request shape:

```text
GET /api/executions?source=temporal&pageSize=<pageSize>&<active filters>
```

The `source=temporal` path is the normal Workflows List data source.

### 5.4 Current row model

The current table row model includes these display fields:

| Field | Meaning |
| --- | --- |
| `workflowId` / `taskId` | Workflow-oriented execution identifier; linked to detail view. |
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
| `entry` | Entry kind, such as `user_workflow` or historical `run`. |
| `dependsOn` | Dependency identifiers. |
| `blockedOnDependencies` | Whether dependency blocking should be summarized. |
| `attentionRequired` | Whether an intervention supplement should be shown under the status pill. |
| `progress` | Optional bounded workflow-owned progress counters plus one `currentStepTitle`; never full step rows, logs, artifacts, stdout/stderr, or diagnostic payloads. |

### 5.5 Current desktop table

The current desktop table columns are:

| Order | Column | Current sort field | Current filter |
| --- | --- | --- | --- |
| 1 | Workflow | `title` | Title text |
| 2 | Status | `status` | Canonical status values |
| 3 | Progress | none | none |
| 4 | Repository | `repository` | Repository text/value |
| 5 | Runtime | `targetRuntime` | Runtime values |
| 6 | Updated | `updatedAt` | Date range |
| 7 | Actions | none | none |

Rules:

1. Header sort buttons sort the current page only until server-authoritative sort is wired through the frontend.
2. The current default frontend sort is `updatedAt` descending by one-minute stability bucket, then queued order descending (`queuedAt || createdAt`).
3. Timestamp columns sort by parsed timestamp. `updatedAt` falls back to the best available execution timestamp before bucketing.
4. Status sorting uses `rawState` or `state` when available.
5. Non-timestamp string columns sort as lowercase strings.
6. Ties after updated bucket and queued order sort by workflow identifier descending.
7. Each status cell renders an `ExecutionStatusPill` using `rawState || state || status`, followed by compact status supplement text when needed.
8. Rows blocked on dependencies show dependency text under the status pill, for example `Blocked by 1 prerequisite`.
9. Rows requiring intervention show `Intervention requested` under the status pill.
10. Failed rows do not repeat `Failed - needs review`, and awaiting-external rows do not repeat `Waiting on external response` when the text merely restates the status.
11. The Progress column renders compactly from `row.progress`: `{succeeded}/{total} · {currentStepTitle}`, `{succeeded}/{total} complete`, `{succeeded}/{total} · Failed at {currentStepTitle}`, or `—` when progress is missing or has no usable total.
12. The list page must not fetch per-row step details to populate Progress.
13. The `Started` timestamp is intentionally not shown in the list presentation.

### 5.6 Current mobile cards

The current mobile layout renders a card list with:

- title link;
- status pill with the same dependency/intervention supplements used by the desktop status cell;
- field grid for Progress, ID, Runtime, Repository, and Updated;
- full-width `View details` card action;
- row actions when workflow row actions are enabled.

### 5.7 Empty, loading, error, and pagination states

Rules:

1. Loading state shows `Loading workflows...`.
2. API errors render a visible error notice.
3. Empty first pages show `No workflows found for the current filters.`
4. Empty later pages keep the previous-page button enabled.
5. Pagination uses an opaque `nextPageToken` plus a client-side cursor stack for previous-page navigation.
6. The results footer shows row range and count when available.
7. Page size is controlled through the shared `PageSizeSelector`.
8. The current-page-only sort notice remains visible when the table has rows and frontend sorting is not server-authoritative.

---

## 6. Target page layout

The target page layout keeps the current column-filter direction and extends it to Progress.

| Surface | Target contents |
| --- | --- |
| Header / notices | Feature-disabled notice, ignored-scope notice, validation errors, cursor recovery notices, and page-level live-update state. |
| Active query row | Active filter chips, clear-all/reset action, optional saved-view chip. No Scope, Workflow Type, Status, Entry, or Repository top-level form controls. |
| Results toolbar / footer | Page summary, page size, pagination, current sort-scope notice when applicable. |
| Desktop table | Sortable and filterable column headers where a column supports those behaviors. |
| Advanced filters | Full filter set, including hidden/optional fields and all mobile filters. |
| Mobile cards | Existing card presentation, with filter affordances available through the mobile filter sheet. |
| View options | Density, column visibility, and live-update preference. |

Rules:

1. Repository filtering stays in the Repository column/drawer filter.
2. Status filtering stays in the Status column/drawer filter.
3. Runtime filtering stays in the Runtime column/drawer filter.
4. Progress sorting and filtering must be added to the Progress column and advanced/mobile filters.
5. Scope, workflow type, and entry controls stay out of the normal Workflows List page instead of being represented by a `Kind` column.
6. The normal page always queries the user-visible Workflow-run scope; system workflows are not available from the ordinary Workflow table.
7. Active filters are still summarized in a row of chips so users do not have to inspect every header.
8. Filter chips must be clickable and reopen the corresponding column filter.
9. A clear/reset action remains available when any filter is active.
10. Live updates remain page behavior, not a table filter.
11. Page size and pagination remain result-window controls, not filters.

---

## 7. Column set

The target desktop table uses this default column model:

| Default visibility | Column | Primary field | Sort | Filter |
| --- | --- | --- | --- | --- |
| Visible | Workflow | `title` plus compact workflow id | Yes | Text search |
| Visible | Status | `rawState || state || status` plus compact supplements | Yes | Canonical status checklist |
| Visible | Progress | `progress` bounded counters and current step title | Yes | Completion range, buckets, signals, current-step text, blanks |
| Visible | Repository | `repository` | Yes | Value checklist, text/prefix search, blanks |
| Visible | Runtime | `targetRuntime` | Yes | Value checklist, blanks |
| Visible | Updated | `updatedAt` | Yes | Date range, relative dates |
| Optional | Target skill | `targetSkill` plus `taskSkills` | Yes | Value checklist, blanks |
| Optional | Integration | `integration` | Yes | Value checklist, blanks |

Rules:

1. The normal Workflows List table does not include a `Kind` column.
2. The normal table does not include `Workflow Type` or `Entry` columns by default.
3. The default query is the Workflow-run list. In current API terms, this is equivalent to `WorkflowType = MoonMind.UserWorkflow` and `mm_entry = user_workflow`. Historical `entry=run` links are compatibility inputs, not the canonical query state.
4. System workflow rows must not appear in the normal Workflow table, even through column filters or old URL parameters.
5. Manifest ingest rows should stay on the Manifests page unless a separate user-workflow diagnostics view is explicitly designed.
6. The table may hide optional columns by default to preserve width, but optional columns must not reintroduce ordinary access to system workflow browsing.
7. The mobile filter sheet must expose the same filterable Workflow columns as desktop, including Progress after Progress filtering is implemented.
8. The table must not expose raw Temporal Visibility query syntax to ordinary users.
9. Runtime and Target skill filters are backed by `mm_target_runtime` and the
   singular primary `mm_target_skill` only when those Search Attributes are
   registered. During migration, missing attributes produce degraded facets or
   empty filtered results rather than a page-level 503.

### 7.1 Admin diagnostics escape hatch

System and all-workflow browsing is useful for debugging, but it is not part of the normal Workflows List UX.

Rules:

1. System workflows belong in an admin diagnostics surface such as Settings -> Operations -> Workflow Diagnostics, `/workflows/diagnostics`, or a similarly explicit route.
2. A diagnostics surface may expose workflow type, entry, owner, namespace, run ID, raw Temporal status, and system workflow filters because its purpose is platform debugging.
3. Diagnostics access must be permission-gated. Ordinary users cannot widen `/workflows` into system workflow visibility by editing URL parameters.
4. If compatibility routes or query parameters such as `scope=system` are still accepted, the normal Workflows List page must either ignore them safely, redirect authorized admins to diagnostics, or show a recoverable message explaining that system workflows moved to diagnostics.
5. The product contract for `/workflows` remains Workflow-oriented even if the underlying `/api/executions` endpoint can list broader workflow scopes.

---

## 8. Column header interaction model

Each sortable/filterable header is a compound control with two distinct targets:

1. **Sort button** — the column label area.
2. **Filter button** — a funnel icon area.

Example conceptual header:

```text
Progress ▼  [filter icon]
```

Rules:

1. Activating the label toggles sorting for that column.
2. Activating the filter icon opens the column filter popover.
3. Sorting must not clear filters.
4. Filtering must not clear the current sort unless the sorted column becomes unavailable.
5. A sorted column shows `▲` for ascending or `▼` for descending.
6. A filtered column shows an active filter icon, visually distinct from an unfiltered icon.
7. A column with both sort and filter active shows both indicators.
8. Header controls must preserve `aria-sort` behavior for the sort target.
9. The filter target must expose accessible state such as `Filter Runtime. No filter applied.`, `Filter Status. Filter active: excluding canceled.`, or `Filter Progress. Filter active: 25 to 75 percent complete.`
10. Progress gets the same split sort/filter affordance as other sortable and filterable columns.

### 8.1 Sort behavior

Current frontend sort behavior is current-page-only. Target long-term behavior is server-authoritative sorting so sort order remains stable across pages and shared links.

Rules:

1. Clicking an unsorted timestamp column defaults to descending.
2. Clicking an unsorted text or value-list column defaults to ascending.
3. Clicking an unsorted Progress column defaults to descending, meaning most complete first.
4. Clicking the currently sorted column toggles ascending and descending.
5. Only one primary sort is required for this page.
6. Multi-column sorting is out of scope for the first Progress version.
7. While a column uses client-side current-page sorting, the UI must not present the order as a global result ordering guarantee.
8. Once a column is server-sortable, `sort` and `sortDir` should be included in shareable URL state and API requests for that column.

### 8.2 Progress sort semantics

Progress sorting is based on a derived numeric completion value, not the rendered Progress string.

Derived value:

```text
progressPct = progress.total > 0
  ? clamp(progress.succeeded / progress.total, 0, 1)
  : null
```

Product labels:

| Sort state | Meaning |
| --- | --- |
| Progress ascending | Least complete first. |
| Progress descending | Most complete first. |

Rules:

1. `progress.succeeded` is the numerator because it matches the displayed `{succeeded}/{total}` copy.
2. Missing progress, `null` progress, and `total <= 0` are Progress blanks.
3. Progress blanks sort last in both ascending and descending sorts unless the user explicitly filters to blanks.
4. Progress sorting must not use `currentStepTitle` as the primary sort value because step titles are high-cardinality, transient text.
5. Progress sorting must not reinterpret workflow outcome. Failed, canceled, and completed workflow outcomes remain Status semantics.
6. Tie-breakers should be deterministic:
   1. progress percentage;
   2. `progress.succeeded`;
   3. `progress.total`;
   4. `progress.updatedAt || updatedAt`;
   5. workflow identifier descending.
7. Server-side Progress sorting requires a materialized or indexed derived progress value. The frontend must not fetch all pages or per-row step ledgers to simulate a global Progress order.

---

## 9. Filter controls

The filter popover is an anchored, keyboard-accessible panel. It is not a native `select` dropdown in the target design, even if the current implementation uses simpler controls for some drawer sections.

### 9.1 Common popover structure

Each value-list filter popover contains:

1. column title;
2. current filter summary;
3. sort commands when the column is sortable;
4. value search input when values are searchable;
5. `Select all` checkbox when the filter is value-list based;
6. scrollable value checklist when the filter is value-list based;
7. blank-value row when the field can be blank;
8. `Clear` or `Reset` action;
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
[✓] Jules (3)
[✓] Blanks (2)

Clear     Cancel     Apply
```

Rules:

1. Checkbox and input changes are staged locally until `Apply` is activated.
2. `Cancel`, `Escape`, or outside click closes the popover without applying staged changes.
3. `Clear` removes the filter for that column and applies immediately or stages a clear state that `Apply` confirms; the chosen behavior must be consistent across columns.
4. Search filters the available checklist values inside the popover; it does not filter table rows until the user applies a value selection or text filter.
5. Value rows show display labels and counts when count data is available.
6. The popover may support an `Only` quick action on value hover/focus for power users, but the row checkbox remains the primary interaction.
7. The menu should show status pills for Status values and human-readable runtime labels for Runtime values.
8. Long value lists must be virtualized or paginated.
9. Value labels must never render untrusted HTML.

### 9.2 Status filter

The Status filter is a value-list popover backed by lifecycle state values.

Current and target status options include the canonical lifecycle states plus intervention compatibility where present in the UI:

```text
scheduled
initializing
waiting_on_dependencies
planning
awaiting_slot
executing
proposals
awaiting_external
intervention_requested
finalizing
completed
failed
canceled
```

Rules:

1. The checklist uses the canonical status order above.
2. Each row shows the same display text and pill styling used in table rows where possible.
3. Filtering by Status maps to MoonMind lifecycle state, not the coarse dashboard status.
4. The filter must tolerate historical rows where `rawState`, `state`, and `status` differ by using the same display precedence as the table: `rawState || state || status`.
5. If backend lifecycle enums and frontend display options diverge, the UI may show compatibility values but the API must validate and report unsupported values clearly.

### 9.3 Runtime filter

The Runtime filter is a value-list popover backed by runtime identifiers.

Rules:

1. The checklist stores raw runtime identifiers such as `codex_cli`.
2. The checklist displays human-readable labels such as `Codex CLI`.
3. Blanks are represented as `—` and can be included or excluded.
4. Runtime filtering is part of the normal Workflows List filter set.

### 9.4 Repository filter

The Repository filter combines value selection and text filtering.

Rules:

1. The default mode is value checklist for repositories present in the filtered result universe when facet data is available.
2. The popover search input filters repository values in the checklist.
3. A secondary text mode supports the current repository text filter. Because Temporal Visibility does not support arbitrary substring matching for Keyword fields, the current `repoContains` parameter behaves as a prefix/text filter even though the historical name says `Contains`.
4. Existing legacy `repo=<value>` links map to a Repository text filter.
5. Blanks can be included or excluded.

### 9.5 Date filters

Scheduled, Updated, Created, and Finished use date filter popovers.

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
2. `Updated` filters the same timestamp that the list displays as Updated.
3. `Finished` blanks represent active or unfinished work.
4. `Scheduled` blanks represent unscheduled immediate work.

### 9.6 Text filters

ID and Title use text-oriented filters.

Rules:

1. ID supports exact ID matching and prefix/contains matching according to backend support.
2. Title supports word-token matching under the current Temporal Visibility implementation; product copy may say "contains words" rather than promising arbitrary substring matching.
3. Text filtering should be debounced for preview counts if preview counts are shown.
4. Applying a text filter resets pagination to the first page.
5. Text filters must trim leading/trailing whitespace for query behavior while preserving the user-entered text in the input until apply/cancel.

### 9.7 Progress filter

The Progress filter is a structured filter backed by the bounded `progress` summary. It must not filter by the rendered string and must not fetch full step rows.

Required controls:

| Control | Purpose |
| --- | --- |
| Sort least complete first | Sets `sort=progressPct&sortDir=asc` when server-authoritative sort is enabled, or current-page Progress ascending before that rollout. |
| Sort most complete first | Sets `sort=progressPct&sortDir=desc` when server-authoritative sort is enabled, or current-page Progress descending before that rollout. |
| Completion from | Inclusive lower bound, 0–100 percent. |
| Completion to | Inclusive upper bound, 0–100 percent. |
| Buckets | Static checklist for human-friendly completion buckets. |
| Signals | Static checklist for meaningful progress counters. |
| Current step title | Text search against `progress.currentStepTitle`. |
| No progress data | Blank include/exclude control for rows with missing progress or no usable total. |

Representative layout:

```text
Progress
All progress values

Sort least complete first
Sort most complete first

Completion
From [ 0 ] %   To [ 100 ] %

Buckets
[ ] Not started
[ ] In progress
[ ] Complete
[ ] No progress data

Signals
[ ] Has running step
[ ] Waiting on external progress
[ ] Reviewing
[ ] Has failed steps
[ ] Has skipped steps
[ ] Has canceled steps

Current step title
[contains text...]

Clear     Cancel     Apply
```

Bucket definitions:

| Bucket | Definition |
| --- | --- |
| No progress data | `!progress || progress.total <= 0` |
| Not started | `total > 0`, no succeeded/failed/skipped/canceled steps, and no active running/awaiting/reviewing step. |
| In progress | `total > 0`, not complete, and some work has started or is actively waiting/reviewing/running. |
| Complete | `total > 0 && succeeded >= total`. |

Signal definitions:

| Signal | Definition |
| --- | --- |
| `running` | `progress.running > 0` |
| `awaiting_external` | `progress.awaitingExternal > 0` |
| `reviewing` | `progress.reviewing > 0` |
| `has_failed_steps` | `progress.failed > 0` |
| `has_skipped_steps` | `progress.skipped > 0` |
| `has_canceled_steps` | `progress.canceled > 0` |

Rules:

1. Completion range filtering uses `progressPct`, not string comparison.
2. Completion percent is derived from `succeeded / total` and clamped to the inclusive range 0–100.
3. Progress blanks are rows with missing progress, null progress, or `total <= 0`.
4. Bucket selections use OR semantics within Progress.
5. Signal selections use OR semantics within the signal group.
6. The Progress filter as a whole ANDs together its enabled subfilters. Example: `Progress 25–75%` plus `Has failed steps` means rows must satisfy both.
7. `Current step title` filtering is for the single bounded `currentStepTitle` string only. It must not search full step detail, logs, artifacts, stdout/stderr, or diagnostic payloads.
8. Progress filtering must remain useful for live rows whose `currentStepTitle` changes; active filters should re-evaluate on refresh without losing staged filter edits.
9. Progress filter labels must use product copy such as `Waiting on external progress`, not raw counter names such as `awaitingExternal`.

---

## 10. Selection semantics

Column filters use AND semantics across columns and OR semantics within a single value-list column.

Examples:

| User selection | Meaning |
| --- | --- |
| Status = `executing` and `planning` | Show rows whose state is executing OR planning. |
| Runtime = `codex_cli`; Status = `failed` | Show rows whose runtime is Codex CLI AND state is failed. |
| Repository excludes `owner/archived` | Show all repositories except `owner/archived`, subject to other filters. |
| Progress = 25–75%; Signal = `has_failed_steps` | Show rows that are 25–75% complete AND have at least one failed step. |
| Progress bucket = `in_progress` or `not_started`; Status = `executing` | Show executing rows whose progress is either in progress OR not started. |

### 10.1 Include mode and exclude mode

The UI must distinguish two cases:

| Mode | How the user gets there | URL/API meaning |
| --- | --- | --- |
| Include mode | User selects a subset from none, uses `Only`, or unchecks many values until a small positive set remains. | Include only the selected values. |
| Exclude mode | User starts from all values and deselects one or more unwanted values. | Include all current and future values except the deselected values. |

Rules:

1. Deselecting one status such as `canceled` from an otherwise-all checklist should create an exclude filter: `not canceled`.
2. Exclude mode allows new values that arrive through live updates to appear automatically.
3. Include mode hides new values until the user explicitly selects them.
4. `Select all` clears the column filter.
5. Deselecting all values is allowed only when the user confirms that the result will be empty, or the UI prevents the impossible state and suggests clearing the filter instead.
6. Progress buckets and Progress signals may support include and exclude mode, but the first implementation may ship include-only signals if the API contract clearly rejects unsupported exclude params.

---

## 11. Active filter chips

Active filters are summarized above the results table or in the mobile filter summary.

Representative chips:

```text
Status: not canceled
Runtime: Codex CLI +1
Repository: MoonLadderStudios/MoonMind
Finished: blank
Progress: 25–75%
Progress: has failed steps
Progress step: tests
```

Rules:

1. Every active column filter has a chip.
2. Clicking a chip opens the corresponding column filter popover or drawer section.
3. Each chip has a remove action that clears only that column filter.
4. Clear/reset filters clears every column filter and restores the default Workflow-run view.
5. Chips use product labels, not raw API parameter names.
6. Chips must remain visible on mobile through a horizontally scrollable row or compact filter summary button.
7. Progress subfilters may be summarized as one chip when compactness matters, for example `Progress: 25–75%, failed steps`, but the chip must still reopen the Progress filter.

---

## 12. URL state

The URL remains the shareable source of client-visible query state.

Current frontend rule:

- Until server-authoritative sorting is fully adopted by the Workflows List UI, `sort` and `sortDir` are not written by the frontend and are not required for list-context links.

Target server-authoritative rule:

| URL state | Purpose |
| --- | --- |
| `limit` | Page size. |
| `nextPageToken` | Current pagination cursor. |
| `sort` | Current server-authoritative sort field. |
| `sortDir` | `asc` or `desc`. |
| column filter params | Active column filters. |

### 12.1 Canonical filter encoding

The API and URL should support multi-value include and exclude filters where meaningful.

Representative URL shapes:

```text
/workflows?stateNotIn=canceled&targetRuntimeIn=codex_cli,claude_code&limit=50
/workflows?progressPctFrom=25&progressPctTo=75&progressSignalIn=has_failed_steps&sort=progressPct&sortDir=desc
```

Recommended parameters:

| Parameter | Meaning |
| --- | --- |
| `stateIn` / `stateNotIn` | Canonical lifecycle state values. |
| `targetRuntimeIn` / `targetRuntimeNotIn` | Runtime identifiers. |
| `targetSkillIn` / `targetSkillNotIn` | Skill identifiers. |
| `repoIn` / `repoNotIn` | Exact repository values. |
| `repoContains` | Repository text filter; current Temporal-backed behavior is prefix-like. |
| `integrationIn` / `integrationNotIn` | Integration values. |
| `workflowId` / `workflowIdContains` | ID exact or text filter. |
| `titleContains` | Title word-token text filter. |
| `scheduledFrom` / `scheduledTo` | Scheduled timestamp bounds. |
| `updatedFrom` / `updatedTo` | Updated timestamp bounds. |
| `createdFrom` / `createdTo` | Created timestamp bounds. |
| `finishedFrom` / `finishedTo` | Finished timestamp bounds. |
| `<field>Blank` | Include or exclude blanks for fields where blank is meaningful. |
| `progressPctFrom` / `progressPctTo` | Inclusive Progress completion percentage bounds, 0–100. |
| `progressBucketIn` / `progressBucketNotIn` | Progress buckets: `not_started`, `in_progress`, `complete`. |
| `progressSignalIn` / `progressSignalNotIn` | Progress signals: `running`, `awaiting_external`, `reviewing`, `has_failed_steps`, `has_skipped_steps`, `has_canceled_steps`. |
| `progressStepTitleContains` | Text filter for `progress.currentStepTitle`. |
| `progressBlank` | Include or exclude Progress blanks. |
| `sort=progressPct` | Sort by derived Progress completion percentage. |

Rules:

1. Values in comma-separated lists must be URL-encoded.
2. If a value can contain commas in the future, the client and API must support repeated parameters as an equivalent representation.
3. The API must reject contradictory include and exclude filters on the same field with a clear validation error.
4. The browser must normalize empty lists away rather than sending no-op filters.
5. Filter changes reset `nextPageToken` and the previous-page cursor stack.
6. Sort changes reset `nextPageToken` and the previous-page cursor stack when sort is server-authoritative.
7. When sort is current-page-only, sort changes do not modify URL state.

### 12.2 Backward compatibility

Existing URLs must continue to fail safe:

| Existing parameter | Desired mapping |
| --- | --- |
| `scope=tasks` | Default Workflow-run view. No visible column filter is required. |
| `scope=user` | Prefer the default Workflow-run view on `/workflows`; manifest ingest belongs on the Manifests page or diagnostics. |
| `scope=system` | Not honored by the normal Workflows List page. Authorized admins may be redirected to diagnostics; ordinary users stay in the default Workflow-run view or see a recoverable message. |
| `scope=all` | Not honored by the normal Workflows List page. Authorized admins may be redirected to diagnostics; ordinary users stay in the default Workflow-run view or see a recoverable message. |
| `workflowType=MoonMind.UserWorkflow` | Default Workflow-run view when paired with `entry=user_workflow`, `entry=run`, or no entry. |
| `workflowType=MoonMind.ManifestIngest` | Redirect to the Manifests page or show a recoverable message; do not add a `Workflow Type` column to the Workflow table. |
| `workflowType=<system value>` | Not honored by the normal Workflows List page; use admin diagnostics when authorized. |
| `state=<value>` | Status column include filter for one value. |
| `entry=user_workflow` | Default Workflow-run view. No visible column filter is required. |
| `entry=run` | Historical alias for the default Workflow-run view. |
| `entry=manifest` | Redirect to the Manifests page or show a recoverable message. |
| `repo=<value>` | Repository text filter. |
| legacy `sort` / `sortDir` while frontend sort is current-page-only | Dropped or ignored so old links do not imply global order. |
| legacy `sort=progress` | Normalize to `sort=progressPct` only after server-authoritative Progress sort exists; otherwise drop or ignore with current-page-only behavior. |

Rules:

1. Existing query parameters remain accepted on load so old shared links do not break.
2. Compatibility handling must never reveal system workflows in the ordinary Workflows List page.
3. After the user changes filters in the new UI, the URL should rewrite to the new canonical Workflow-column filter encoding.
4. Shared old links should either preserve meaning inside the Workflow-focused page, redirect to the more appropriate page, or explain why the old workflow scope moved.
5. Progress compatibility params must not force a per-row step-ledger fetch.

---

## 13. API and data requirements

The `/api/executions` list endpoint is the server authority for the normal Workflows List page. Current frontend filtering already relies on list query parameters and facet data for non-Progress columns. Progress sorting/filtering requires additional derived query support.

### 13.1 List query requirements

The list endpoint should support:

1. server-authoritative sort field and direction;
2. multi-value include filters;
3. multi-value exclude filters;
4. text filters for ID, title, repository, and Progress current step title;
5. date range filters;
6. blank/null filters where meaningful;
7. Progress completion range filters;
8. Progress bucket and signal filters;
9. deterministic pagination under active filters and sort;
10. count and count mode for the fully filtered result.

Rules:

1. Filters across columns use AND semantics.
2. Multiple values within one column use OR semantics.
3. Exclude filters are evaluated after field normalization.
4. Display labels are never sent as canonical filter values when raw values exist.
5. The API remains the authority for access control; users cannot widen their visibility through filter params.
6. The normal Workflows List query is always bounded to user-visible Workflow Executions. Backend list support for broader workflow scopes must not leak into this page.
7. Progress query semantics must be evaluated from bounded execution progress summary data, not full step-ledger hydration.

### 13.2 Facet query requirements

The UI needs facet data so a filter popover can show values and counts beyond the current page.

Recommended endpoint pattern:

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
5. Progress buckets and signals are static lists; counts are useful but optional for the first Progress implementation.
6. Progress current-step title should not be exposed as a full value facet because it is high-cardinality and transient.
7. Facet counts must reflect the current query context.
8. If exact counts are expensive, the response may set `countMode` to an estimated or unknown mode; the UI must label those counts accordingly or omit counts.
9. Large facets may be paginated and searched server-side.
10. Facet failure must not break the table; the UI can fall back to values in the currently loaded page with a visible `current page values only` notice.
11. Facet results must not include system-only workflow values or counts on the normal Workflows List page.

### 13.3 Progress data materialization

Progress sorting and filtering cannot be server-authoritative if Progress exists only as live display data loaded after the current page is fetched.

Required derived fields:

| Derived field | Meaning |
| --- | --- |
| `progressPct` or `progressPctBps` | Completion percent derived from `succeeded / total`; basis points are preferred for storage. |
| `progressTotal` | `progress.total`. |
| `progressSucceeded` | `progress.succeeded`. |
| `progressFailed` | `progress.failed`. |
| `progressRunning` | `progress.running`. |
| `progressAwaitingExternal` | `progress.awaitingExternal`. |
| `progressReviewing` | `progress.reviewing`. |
| `progressSkipped` | `progress.skipped`. |
| `progressCanceled` | `progress.canceled`. |
| `progressBucket` | Derived bucket such as `not_started`, `in_progress`, or `complete`. |
| `progressSignals` | Derived keyword list for signal filters. |
| `progressCurrentStepTitleSearch` | Projection or bounded query-model text/token field for `progress.currentStepTitle`; not a Temporal Visibility Search Attribute. |
| `progressUpdatedAt` | Last meaningful progress mutation. |
| `progressBlank` | Whether progress is missing or has no usable total. |

Rules:

1. Numeric and categorical derived Progress fields may be stored in the projection, indexed as Temporal Visibility Search Attributes only where explicitly approved by the Visibility registry, or served from another bounded query model.
2. `progress.currentStepTitle`, `progressCurrentStepTitleSearch`, and any title tokens are not eligible for Temporal Visibility indexing because they are high-cardinality display prose that may come from workflow plans or user input.
3. Current-step-title search must use projection storage or another bounded query model that is not operator-visible Temporal metadata.
4. Any future Visibility registry update for Progress must explicitly exclude free-text, display-only prose, and high-cardinality user/plan text.
5. The list page must not call `GET /api/executions/{workflowId}/steps` to compute Progress sort/filter.
6. The list page must not fetch every page and sort/filter in the browser to simulate global Progress behavior.
7. The bounded `progress` summary remains display-safe. It must not inline full step rows, logs, artifacts, stdout/stderr, provider payloads, or diagnostics.
8. Running workflows may still refresh live Progress display data, but server filters and counts must use a stable queryable representation.

---

## 14. Live updates and filter stability

Live updates continue to be useful after column filters, but they must not disrupt staged menu choices.

Rules:

1. When no filter popover or drawer is open, live updates may refetch according to the configured polling interval.
2. When a filter popover or drawer is open, the checklist snapshot should remain stable until the popover closes or the user explicitly refreshes values.
3. New rows that match active filters may appear after polling.
4. Rows that no longer match active filters may disappear after polling.
5. Include-mode filters do not automatically include newly discovered values.
6. Exclude-mode filters do include newly discovered values unless the value is excluded.
7. Progress filters re-evaluate as live progress changes; for example, a row may leave `Not started` and enter `In progress`.
8. The page may show subtle copy such as `Values updated after you opened this filter` when a facet changes while a popover is open.
9. Live updates must not overwrite staged, unapplied filter changes.

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
9. Active filter chips expose remove buttons with names such as `Remove Status filter` or `Remove Progress filter`.
10. Mobile filter sheet controls must be equivalent to desktop controls for screen-reader and keyboard users.
11. Color must not be the only indicator of active sort, active filter, selected status, or selected Progress signal.
12. Progress range controls must expose percent units in their accessible names.
13. Progress signal labels must be human-readable, for example `Has failed steps` instead of `failed greater than zero`.

---

## 16. Mobile behavior

The mobile card layout remains the primary narrow-screen presentation. Column header filters are adapted into a mobile filter sheet.

Rules:

1. The mobile results toolbar includes a `Filters` button when the table header is not visible.
2. The mobile filter sheet lists the same filterable columns as desktop, including Progress after Progress filtering is implemented.
3. Each column row opens the same filter editor used by desktop, adapted to full-screen or bottom-sheet layout.
4. Active chips remain visible on mobile through a horizontally scrollable chip row or compact filter summary.
5. Mobile cards continue to show Progress in the field grid using the same compact display string as desktop.
6. Mobile Progress filtering must not require a table header to be visible.

---

## 17. Progress test contract

The Progress sort/filter implementation should preserve these testable behaviors:

1. The Progress column exposes the same separate sort and filter affordances used by other sortable/filterable columns.
2. Progress sort uses derived completion percent, not rendered cell text or current-step title text.
3. Progress blanks sort last in both ascending and descending directions.
4. Progress filter state serializes to canonical query params for completion range, buckets, signals, current-step title search, and blanks.
5. Active Progress filters render product-labeled chips and reopen the Progress filter when activated.
6. Mobile filters expose Progress controls even when desktop table headers are not visible.
7. The Workflows List page does not request per-row step details or full step ledgers to compute Progress display, sort, or filter behavior.
8. Current-page-only Progress sorting, if used before server-authoritative rollout, keeps sort out of URL/API state and keeps the current-page-only notice visible.
9. Server-authoritative Progress sorting, once enabled, resets pagination and includes `sort=progressPct` plus `sortDir` in URL/API state.
10. Backend validation rejects contradictory Progress include/exclude filters with a clear validation error.
