# Workflows List Page

Owners: MoonMind Engineering  
Covers: dashboard Workflows list route, execution-list controls, table sorting, column filters, filter URL state, Google Sheets-like list filtering, Provider Profile presentation, and Progress column sort/filter semantics

---

## 1. Purpose

This document describes the MoonMind **Workflows List** page.

The page helps operators inspect Temporal-backed MoonMind Workflow Executions in a Workflow-oriented table. It supports fast scanning, stable pagination, clear status visibility, accessible sorting, shareable filters, and column-level filtering that works the same way in desktop table headers and the mobile filter sheet.

The column filtering model is intentionally similar to Google Sheets filters: each filterable column owns a filter control where the operator can stage changes, search or enter values, include or exclude values, include or exclude blanks when meaningful, clear a column filter, cancel staged edits, and apply the filter.

The ordinary list shows **Provider Profile** (optionally with **Harness** as secondary text in the same cell) rather than a Runtime column. **Backend** identifies Omnigent and belongs in execution details, not another list column. Rows, filters, counts, and facets read one recorded-profile projection (section 7.3), not a relabeled runtime field.

---

## 2. Related docs and implementation surfaces

- [Harness, Provider Profile, and Backend Selection](HarnessProfileBackendSelection.md) — product vocabulary, recorded identity, profile scope, and legacy runtime handling.
- `docs/Api/ExecutionsApiContract.md` — `/api/executions` list contract, lifecycle fields, filters, count, and pagination.
- `docs/Temporal/VisibilityAndUiQueryModel.md` — Temporal Visibility and UI query model.
- `docs/Temporal/StepLedgerAndProgressModel.md` — workflow-owned step ledger and bounded execution progress summary.
- `docs/UI/WorkflowConsoleArchitecture.md` — workflow console shell and shared frontend architecture.
- `docs/UI/DashboardDesignSystem.md` — shared dashboard visual language.
- `docs/UI/WorkflowWorkspaceSidebar.md` — desktop list-to-detail transitions, sidebar list context, and mobile card-to-detail behavior.

Main implementation surfaces:

```text
api_service/api/routers/workflow_console.py
api_service/api/routers/executions.py
frontend/src/entrypoints/workflow-list.tsx
frontend/src/entrypoints/workflow-list.test.tsx
frontend/src/lib/workflowListQuery.ts
frontend/src/lib/workflowListContext.ts
frontend/src/styles/dashboard.css
```

---

## 3. Route and hosting model

The canonical route is `/workflows`. Old `/tasks/*` list routes are not in the route table and do not redirect.

The page is server-hosted by FastAPI and rendered by the shared dashboard React/Vite frontend. The server renders the `workflow-list` page key and runtime dashboard configuration into the boot payload. The route uses a wide data-panel layout because the primary surface is a multi-column execution table.

The browser calls MoonMind APIs only. It does not call Temporal, GitHub, Jira, object storage, or runtime providers directly, so authorization and credential handling stay on the server.

---

## 4. Product stance

The Workflows List page is an operator scanning surface, not a generic Temporal namespace browser.

1. The default view shows ordinary user-created Workflow Executions. It is not a workflow-kind browser and has no `Kind`, `Workflow Type`, or `Entry` column.
2. System workflows (provider-profile managers, internal monitors, maintenance workflows, and other platform-owned executions) are hidden from this page; they belong in an admin diagnostics surface.
3. Retired Manifest-ingest rows never appear in the table; there is no Manifests page.
4. Sorting and filtering are table behaviors expressed on the relevant columns rather than as detached dropdowns above the table.
5. The active query is made obvious through column filter indicators, active filter chips, and URL state.
6. Live updates are useful but do not disturb an in-progress filter selection.

---

## 5. Page layout

| Surface | Contents |
| --- | --- |
| Header / notices | Feature-disabled notice, ignored-scope notice, validation errors, cursor-reset recovery notices, and page-level live-update state. |
| Active query row | Active filter chips and a clear/reset action. No Scope, Workflow Type, Entry, or top-level Status/Repository form controls. |
| Results toolbar / footer | Row range and count, page size (shared `PageSizeSelector`), pagination, and the current-page sort notice when it applies. |
| Desktop table | Sortable and filterable column headers. |
| Advanced filters drawer | The full filter set, including fields not visible in the table (ID, Skill, Scheduled, Created, Finished). |
| Mobile cards | Card presentation with filters available through the mobile filter sheet. |
| View options | Density, column visibility, and live-update preference. |

The page reads `dashboardConfig.pollIntervalsMs.list` (default five seconds) and is disabled when `dashboardConfig.features.temporalDashboard.listEnabled === false`.

### 5.1 Rows and status cells

Each row comes from the `/api/executions` list response: workflow identifier and title, repository, integration, skill fields, lifecycle state (`rawState`, `state`, `status`), timestamps, dependency and intervention flags, the compact Provider Profile summary, and an optional bounded `progress` summary (counters plus one `currentStepTitle`, never full step rows, logs, artifacts, stdout/stderr, or diagnostics).

- The status cell renders an `ExecutionStatusPill` from `rawState || state || status`, plus compact supplements: dependency blocking (for example `Blocked by 1 prerequisite`) and `Intervention requested`. Supplements that merely restate the status are not shown.
- The Progress cell renders `{completed}/{total} · {currentStepTitle}`, `{completed}/{total} complete`, `{completed}/{total} · Failed at {currentStepTitle}`, or `—` when progress is missing or has no usable total. The page never fetches per-row step details to populate it.
- `Started` is not shown in the list presentation.

### 5.2 Row actions

When workflow actions are enabled, each desktop Actions cell and mobile card exposes a single three-dot `More actions` menu. Cancel, Rerun, and Remediate live in that menu with the other workflow actions. Action capabilities load when the operator opens the menu rather than during list rendering, which keeps the list request cheap; loading and unavailable actions show their disabled reasons inside the menu while the trigger stays usable. Desktop and mobile share the same handlers and availability rules. On mobile the open menu stays in the card's flow; on desktop it can extend past the table edges and stays clickable above the results footer.

### 5.3 Empty, loading, error, and pagination states

- Loading shows `Loading workflows...`; API errors render a visible notice.
- An empty page shows `No workflows found for the current filters.` with a `Create a workflow` link to `/workflows/new`. Empty desktop results keep the table header, column filters, sort controls, and column selection so the operator can adjust the query. Empty later pages keep the previous-page button enabled.
- Pagination uses an opaque `nextPageToken` plus a client-side cursor stack for previous-page navigation.

---

## 6. Column set

| Default visibility | Column | Primary field | Sort | Filter |
| --- | --- | --- | --- | --- |
| Visible | Workflow | `title` plus compact workflow id | Yes | Text search |
| Visible | Status | `rawState \|\| state \|\| status` plus supplements | Yes | Canonical status checklist |
| Visible | Progress | bounded `progress` counters and current step title | Yes | Completion range, buckets, signals, current-step text, blanks |
| Visible | Repository | `repository` | Yes | Value checklist, text/prefix search, blanks |
| Visible | Provider Profile | Recorded profile summary with optional secondary Harness | Yes | Stable profile-ID membership plus typed absence states |
| Visible | Updated | `updatedAt` | Yes | Date range, relative dates |
| Optional | Target skill | `targetSkill` plus `taskSkills` | Yes | Value checklist, blanks |
| Optional | Integration | `integration` | Yes | Value checklist, blanks |

Notes:

1. The default query is the Workflow-run list, equivalent to `WorkflowType = MoonMind.UserWorkflow` and `mm_entry = user_workflow`.
2. System workflow rows do not appear through column filters, optional columns, or old URL parameters.
3. The mobile filter sheet exposes the same filterable columns as desktop, including Progress and Provider Profile.
4. Raw Temporal Visibility query syntax is not exposed.
5. Legacy runtime and target-skill filters use the `mm_target_runtime` and singular `mm_target_skill` Search Attributes where registered. Provider Profile is not a new label for `mm_target_runtime`; it has its own recorded-identity projection, and missing query capability is reported truthfully rather than guessed from current profiles.

### 6.1 Admin diagnostics

System and all-workflow browsing is useful for platform debugging but is not part of `/workflows`. A diagnostics surface (for example under Settings -> Operations) may expose workflow type, entry, owner, namespace, run ID, raw Temporal status, and system workflow filters, and is permission-gated. Ordinary URL editing cannot widen `/workflows` into system visibility: any `scope` parameter, or a `workflowType` other than `MoonMind.UserWorkflow` or an `entry` other than `user_workflow`, is ignored and the page shows `Workflow scope filters are not available on Workflows. Showing workflow runs only.` `/api/executions` can list broader scopes, but the normal page always queries the user-visible Workflow-run scope.

---

## 7. Provider Profile identity and projection

### 7.1 What the column shows

The column shows the workflow's recorded selection and supported explicit step-profile variations, not a transient current-agent field. Backend and actual attempt use remain inspectable in details.

| Available record | `selectionState` | Display |
| --- | --- | --- |
| One known profile | `recorded` | Recorded friendly name, or stable ID if no name was recorded; optional secondary Harness. |
| Selection not yet resolved, no profile ID recorded | `pending` | Pending selection. |
| Historical association unavailable, no profile ID recoverable | `not_recorded` | Not recorded. |
| Confirmed no agent profile applies | `not_applicable` | Not applicable. |
| Multiple applicable profiles | `recorded` | Multiple profiles, with a bounded summary; individual step/attempt associations are in details. |

States are mutually exclusive for the workflow summary: any recorded applicable ID yields `recorded`, and the three absence states apply only when no ID is recorded. Projection failure or unavailable coverage is reported as unavailable information and is never classified as an absence state. A missing historical memo and index is `not_recorded`; a present but malformed memo, or an indexed workflow whose memo is missing, is unavailable and the row shows **Unavailable**. Trustworthy IDs in a partial record stay visible.

An authored profile can be displayed before launch without claiming successful acquisition. Renaming, disabling, disconnecting, or removing a live profile does not erase the historical association, because the display name is snapshotted with the admitted selection. Old records that contain only an ID show that ID rather than an invented name. The account is never inferred from runtime, model, provider, or current default, because that would misreport which account actually ran the work.

The list payload carries only the compact summary; the browser does not request per-row profiles, execution details, step ledgers, or Temporal histories, and no credentials, OAuth paths, raw provider payloads, or infrastructure handles enter the list payload. There is one Provider Profile column, not separate Harness, Provider, Backend, Container, and Host columns. Long names wrap or truncate accessibly, and equal names are disambiguated by a compact stable ID.

### 7.2 How the projection is recorded

Admission (`TemporalExecutionService.create_execution` and recurring schedule action writes) records the projection once per user workflow from admitted parameters only: the agent-profile snapshot's `providerProfileRef` (or the top-level Provider Profile `profileId` admission sets to the same ID) plus explicit or resolved step `providerProfileRef` values. Execution-configuration `profileId`, runtime, model, and current defaults never establish an association. Schedule creation, update, and recreation use the same projection builder, and the schedule reconciler repairs missing action projections for future occurrences; already-started executions are not changed.

When admission records `pending` because selection resolves at launch, `MoonMind.AgentRun` reports the granted `providerProfileId` and frozen display-name snapshot through its existing child-to-parent progress signal, and `MoonMind.UserWorkflow` accepts it through the existing child, attempt, and revision fences, turning `pending` into `recorded` while the child is still running. The association survives child cancellation or failure (result metadata is an idempotent fallback); a rejected store write is retried by later accepted progress or terminal metadata; a grant during an operator pause updates identity without resuming the step; and Temporal patch markers keep older payloads replay-safe. A launch on an additional profile adds that association once. Workflows without an admission projection are never back-filled.

| Store | Contents | Used for |
| --- | --- | --- |
| Memo `providerProfile` | `selectionState`, one `{id, label, harness}` entry per distinct association, `profileCount`. The row serializer exposes the first eight entries; ID-only entries omit `label`. | Bounded row summary and facet identities/labels. |
| `mm_provider_profile` (Text Search Attribute) | Space-separated opaque tokens: one state token plus one hashed token per recorded ID, including IDs beyond the display bound. | Filters, counts, and facet counts before pagination. |

Membership is a `Text` attribute rather than a `KeywordList` because Temporal SQL Visibility allows three `KeywordList` attributes and all three are in use. Its tokens are single lowercase alphanumeric terms so every Visibility store matches them exactly. Records admitted before the projection existed have neither store and report `not_recorded`, which means no association is recorded, not that no profile was used. When the namespace has not registered `mm_provider_profile`, Provider Profile filters return a degraded empty list with an unknown count, the facet reports `current_page_fallback`, and the browser says so.

---

## 8. Column header interaction

Each sortable/filterable header has two separate targets: a **sort button** (the label, showing `▲`/`▼` when sorted) and a **filter button** (a funnel icon, visually distinct when active). Sorting and filtering do not clear each other. The filter target exposes state such as `Filter Status. Filter active: excluding canceled.`

### 8.1 Sort behavior

Frontend sorting reorders the current page only. The sort field and direction are kept in dashboard preferences (so they survive table/detail navigation, and resetting preferences restores the default), but are not read from or written to the URL and are not sent to the list API. This avoids implying a global order across the full filtered result set; the footer, header tooltips, and screen-reader hints say `Sorting applies to the current page only.` The list API accepts `sort`/`sortDir`, and server-authoritative sorting would put them in shareable URL state and reset pagination on change.

- The default order is `updatedAt` descending by a one-minute stability bucket, then queued order descending (`queuedAt || createdAt`), then workflow identifier descending. The stability bucket keeps rows from reshuffling on every poll.
- Clicking an unsorted timestamp column sorts descending; text and value-list columns sort ascending; Progress sorts descending (most complete first). Clicking the sorted column toggles direction. There is one primary sort.
- Timestamps sort by parsed time; Status sorts by `rawState` or `state`; strings sort case-insensitively.
- Provider Profile sorts by its display projection with deterministic identity tie-breakers.
- The page never fetches all rows to simulate a global order.

### 8.2 Progress sort semantics

Progress sorts by a derived completion value, not the rendered string:

```text
progressPct = progress.total > 0
  ? clamp(progress.completed / progress.total, 0, 1)
  : null
```

`progress.completed` is the numerator because it matches the displayed `{completed}/{total}`. Missing progress or `total <= 0` are Progress blanks and sort last in both directions. `currentStepTitle` is not a sort key because it is high-cardinality, transient text, and Progress does not reinterpret workflow outcome (failed, canceled, and completed stay Status semantics). Tie-breakers are percentage, `completed`, `total`, `progress.updatedAt || updatedAt`, then workflow identifier descending.

---

## 9. Filter controls

The filter popover is an anchored, keyboard-accessible panel rather than a native `select`.

### 9.1 Common popover structure

A value-list popover contains the column title, current filter summary, sort commands (when sortable), a value search input, `Select all`, a scrollable checklist with counts (for example `OpenAI · Primary (18)`), a blank-value row when the field can be blank, and `Clear`, `Cancel`, and `Apply` actions.

- Changes are staged locally until `Apply`. `Cancel`, `Escape`, or an outside click closes without applying.
- `Clear` behaves the same way across columns.
- Search narrows the checklist inside the popover; it does not filter rows until applied.
- Value rows show labels and counts when available; Status values use pill styling and Provider Profile values use recorded labels with stable-ID disambiguation.
- Long value lists are paginated or virtualized. Value labels never render untrusted HTML.

### 9.2 Status

A checklist of canonical lifecycle states in this order: `scheduled`, `initializing`, `waiting_on_dependencies`, `planning`, `awaiting_slot`, `executing`, `awaiting_external`, `intervention_requested`, `finalizing`, `completed`, `failed`, `canceled`. It filters by MoonMind lifecycle state, not the coarse dashboard status, using the same `rawState || state || status` precedence as the table so historical rows behave consistently. The API validates unsupported values and reports them clearly.

### 9.3 Provider Profile

A value-list popover backed by stable recorded profile IDs, not runtime identifiers or display names, so equal names never collapse distinct accounts.

- Include mode matches a workflow when any recorded member is selected; exclude mode rejects workflows containing any excluded member. Each workflow counts once per facet value even when several steps use that profile.
- `pending`, `not_recorded`, and `not_applicable` are separate selectable state values filtered by `providerProfileStateIn` / `providerProfileStateNotIn`. A blank shortcut covers their aggregate no-recorded-ID bucket. Unknown projection coverage is not known absence.
- Positive ID and state selections combine with OR (for example `profile-a` or Pending); exclusions reject any match; other columns still combine with AND.
- Facets describe authorized workflow records, including profiles that are disabled, disconnected, renamed, or removed from current launch inventory.
- Filtering uses the server query projection, never per-row hydration or browser filtering after pagination. Facet errors preserve selected IDs and states and keep the table usable.
- A runtime constraint from an older link stays visible as a **Legacy runtime** chip and drawer note that can be removed but not newly authored. It is never translated into current profile inventory, because today's profiles say nothing about which account an old run used.

### 9.4 Repository

Combines a value checklist (from facet data when available) with a text mode. Because Temporal Visibility does not support arbitrary substring matching for Keyword fields, `repoContains` behaves as a prefix filter despite its name. Legacy `repo=<value>` links map to the text filter. Blanks can be included or excluded.

### 9.5 Dates

Scheduled, Updated, Created, and Finished use date popovers with newest/oldest sort commands, inclusive From/To bounds, optional relative presets, and blanks for Scheduled and Finished. Display uses local formatting; URL/API values use ISO 8601. `Updated` filters the same timestamp the list displays. Finished blanks mean unfinished work; Scheduled blanks mean unscheduled immediate work.

### 9.6 Text

ID supports exact and prefix/contains matching according to backend support. Title supports word-token matching under Temporal Visibility, so copy says "contains words" rather than promising substring matching. Text filters trim whitespace for the query while preserving the input as typed, and applying one resets pagination.

### 9.7 Progress

A structured filter over the bounded `progress` summary. It never filters on the rendered string or fetches full step rows.

Controls: least/most complete sort commands, inclusive 0–100 percent completion bounds on `progressPct`, bucket and signal checklists, a text search against `progress.currentStepTitle` only, and include/exclude for blanks (no progress data).

| Bucket | Definition |
| --- | --- |
| No progress data | `!progress \|\| progress.total <= 0` |
| Not started | `total > 0`, no completed/failed/skipped/canceled steps, and no active executing/awaiting/reviewing step. |
| In progress | `total > 0`, not complete, and some work has started or is actively waiting/reviewing/executing. |
| Complete | `total > 0 && completed >= total` |

Signals are true when the matching counter is positive: `executing`, `awaiting_external` (shown as `Waiting on external progress`), `reviewing`, `has_failed_steps`, `has_skipped_steps`, `has_canceled_steps`.

Buckets combine with OR, signals combine with OR, and the enabled subfilters combine with AND (for example `25–75%` plus `Has failed steps` requires both).

---

## 10. Selection semantics

Filters combine with AND across columns and OR within a value-list column.

For example, Status = `executing`, `planning` with Provider Profile = `profile-a` shows executing or planning workflows that recorded `profile-a`; Provider Profile excluding Not applicable rejects confirmed no-agent workflows but keeps pending and not-recorded rows.

### 10.1 Include and exclude mode

| Mode | How the operator gets there | Meaning |
| --- | --- | --- |
| Include | Selects a subset, uses `Only`, or unchecks values until a small positive set remains. | Only the selected values. |
| Exclude | Starts from all values and deselects unwanted ones. | All current and future values except those deselected. |

Deselecting `canceled` from an otherwise-all Status list produces `not canceled`. Exclude mode lets new values arriving through live updates appear automatically; include mode hides them until selected. `Select all` clears the column filter. Deselecting everything is either confirmed as an intentionally empty result or prevented with a suggestion to clear the filter.

---

## 11. Active filter chips

Active filters are summarized above the table or in the mobile filter summary, for example `Status: not canceled`, `Provider Profile: OpenAI · Primary +1`, `Finished: blank`, `Progress: 25–75%, failed steps`.

Every active column filter has a chip with product labels rather than API parameter names. Clicking a chip reopens that column's filter; its remove action clears only that column. Clear/reset clears every column filter and restores the default Workflow-run view. On mobile, chips stay visible in a horizontally scrollable row or compact summary.

---

## 12. URL state

The URL is the shareable source of client-visible query state: page size (`limit`), the pagination cursor (`nextPageToken`), and active column filters. Because sorting is current-page-only, `sort` and `sortDir` are not written to the URL.

### 12.1 Filter encoding

The shared executions query builder implements these parameters for list, metrics, and facets.

```text
/workflows?stateNotIn=canceled&providerProfileIdIn=profile-a&providerProfileIdIn=profile-b&limit=50
/workflows?providerProfileStateIn=pending,not_recorded&providerProfileStateNotIn=not_applicable
/workflows?progressPctFrom=25&progressPctTo=75&progressSignalIn=has_failed_steps
```

| Parameter | Meaning |
| --- | --- |
| `stateIn` / `stateNotIn` | Canonical lifecycle states. |
| `providerProfileIdIn` / `providerProfileIdNotIn` | Recorded profile-ID membership; repeated once per exact ID, with commas treated literally. |
| `providerProfileIn` / `providerProfileNotIn` | Comma-separated ID lists retained for existing URLs and saved views; they union with the exact-ID lists. New controls write the exact-ID form. |
| `providerProfileStateIn` / `providerProfileStateNotIn` | Absence states `pending`, `not_recorded`, `not_applicable`. |
| `providerProfileBlank` | `true` includes all three absence states; `false` includes `recorded`. Not combinable with other Provider Profile parameters. |
| `targetRuntimeIn` / `targetRuntimeNotIn` | Legacy runtime identifiers; never a Provider Profile alias. |
| `targetSkill*`, `integration*`, `repoIn` / `repoNotIn`, `repoContains` | Value filters; `repoContains` is prefix-like. |
| `workflowId` / `workflowIdContains`, `titleContains` | ID and title text filters. |
| `<date>From` / `<date>To`, `<field>Blank` | Timestamp bounds for scheduled, updated, created, finished; blank include/exclude. |
| `progressPctFrom` / `progressPctTo`, `progressBucket*`, `progressSignal*`, `progressStepTitleContains`, `progressBlank` | Progress filters from section 9.7. |

Validation and normalization:

- The API rejects contradictory include and exclude values on the same field with a clear error, including an ID or state both included and excluded, unknown state tokens, and `providerProfileBlank` combined with other Provider Profile filters. Silently ignoring or broadening these would show the operator a different result set than the one they asked for.
- The browser drops empty lists instead of sending no-op filters, keeps both Provider Profile include and exclude branches while the mode selector chooses which is being edited, and rewrites `providerProfileBlank` links to the equivalent explicit state list.
- Filter and page-size changes reset `nextPageToken` and the previous-page cursor stack.
- Provider Profile IDs and states round-trip as distinct typed values through URLs, saved views, chips, and staged desktop/mobile selections. State filters run before pagination with the same count semantics as ID filters.

### 12.2 Older links

Existing parameters stay accepted on load so shared links do not break, and none of them reveal system workflows.

| Parameter | Handling |
| --- | --- |
| `workflowType=MoonMind.UserWorkflow`, `entry=user_workflow` | Default Workflow-run view. |
| any `scope`, other `workflowType` or `entry` values (including retired `MoonMind.ManifestIngest`) | Ignored; the page shows the default view with the workflow-scope notice from section 6.1. |
| `state=<value>` | Status include filter for one value. |
| `repo=<value>` | Repository text filter. |
| legacy runtime filter | **Legacy runtime** chip, removable but not newly authored, never relabeled as Provider Profile. |
| `sort` / `sortDir` | Ignored, so old links do not imply a global order. |

After the operator edits filters, the URL rewrites to the canonical encoding while preserving a retained legacy constraint's distinct meaning. Provider Profile column visibility may inherit the old Runtime column preference, but a saved runtime filter never becomes a profile filter. List/detail return links, mobile context, and saved views preserve the actual query.

---

## 13. API and data

`/api/executions` is the server authority for this page. It supports multi-value include/exclude filters, text and date filters, blank filters, Progress range/bucket/signal filters, sort field and direction, deterministic pagination under active filters, and count with count mode.

- The API is the authority for access control; filter params cannot widen visibility, and the normal page query is always bounded to user-visible Workflow Executions.
- Progress query semantics use the bounded progress summary, not step-ledger hydration.
- Provider Profile membership, `selectionState`, display summaries, filtering, count, and pagination share one recorded selection scope, and the ID/state union and exclusions apply before count and pagination.

### 13.1 Facets

Facets let a popover show values and counts beyond the current page:

```text
GET /api/executions/facets?source=temporal&facet=providerProfile&<current filters except Provider Profile>
```

```json
{
  "facet": "providerProfile",
  "items": [
    { "value": "profile-a", "label": "OpenAI · Primary", "count": 18 }
  ],
  "stateItems": [
    { "value": "pending", "label": "Pending selection", "count": 2 },
    { "value": "not_recorded", "label": "Not recorded", "count": 1 },
    { "value": "not_applicable", "label": "Not applicable", "count": 3 }
  ],
  "blankCount": 6,
  "countMode": "exact",
  "truncated": false,
  "nextPageToken": null
}
```

- Facets are scoped by authorization, apply every active filter except the one being opened, and never include system-only values.
- Status and Progress buckets/signals are static lists; Repository, Skill, Integration, and Provider Profile values come from server data (profiles from recorded associations, including ones no longer launchable). Current-step title is not a facet because it is high-cardinality and transient.
- A workflow appears at most once per profile count, but a multi-profile workflow can contribute to several values, so counts need not sum to the workflow total. `stateItems` always lists all three absence states, including zero counts; each absent workflow contributes to exactly one, and `blankCount` is their sum when counts are exact.
- When exact counts are expensive, `countMode` may be estimated or unknown, and the UI labels or omits counts accordingly.
- Provider Profile facets paginate with an explicit **Load more** control that fetches one continuation page per action and merges options by stable ID; the page does not enumerate every page automatically. Continuation keeps an intra-page offset, the Visibility cursor, and already-emitted IDs so pages neither drop nor duplicate options, and the same `pageSize` is used throughout. Older memo projections whose count exceeds their retained associations report `truncated=true`, since hashes cannot restore lost IDs or names.
- Facet failure does not break the table: the UI falls back to current-page values with `Showing current page values only`, keeps already-loaded options and selected IDs/states, and lets the operator retry. Closing the editor or changing scope cancels stale requests.

### 13.2 Progress materialization

Server-side Progress sorting and filtering need queryable derived fields (completion percent, counters, bucket, signals, blank flag, a bounded title search field) rather than live display data. Numeric and categorical fields may live in the projection, in registry-approved Temporal Search Attributes, or in another bounded query model.

- `currentStepTitle` and its search tokens are never indexed in Temporal Visibility, because they are high-cardinality display prose that may come from workflow plans or user input; title search uses projection storage or another bounded model.
- The list page never calls `GET /api/executions/{workflowId}/steps` or fetches every page to compute Progress sort or filter.
- The bounded `progress` summary stays display-safe: no step rows, logs, artifacts, stdout/stderr, provider payloads, or diagnostics.

---

## 14. Live updates and filter stability

The page refetches on the configured polling interval, so rows that start or stop matching active filters (including Progress filters) appear or disappear. While a popover or drawer is open, its checklist snapshot stays stable until it closes, and live updates never overwrite staged, unapplied changes. Include-mode filters do not pick up newly discovered values; exclude-mode filters do unless the value is excluded.

---

## 15. Accessibility

Sort and filter controls are separately keyboard-reachable; headers keep `aria-sort`; filter buttons expose `aria-haspopup`, `aria-expanded`, and descriptive names; popovers use dialog semantics, take focus on open, and return it to the filter button on close (`Escape` cancels, `Enter` on `Apply` applies). Chip remove buttons have names like `Remove Provider Profile filter`. Mobile controls are equivalent for keyboard and screen-reader users. Color is never the only indicator of sort, filter, or selection state, and Progress range inputs include percent units in their accessible names.

---

## 16. Mobile behavior

Cards are the narrow-screen presentation: title link, status pill with the same supplements as desktop, a field grid with Progress, ID, Provider Profile (with optional Harness), Repository, and Updated, a full-width `View details` action, and the row actions menu.

A `Filters` button opens a filter sheet listing the same filterable columns as desktop, each opening the same editor in a full-screen or bottom-sheet layout, so mobile filtering never depends on a visible table header. Active chips remain visible. Long or equal-name profiles and unavailable metadata keep readable labels, stable identity, and usable controls without page overflow.

---

## 17. Behavior covered by tests

`frontend/src/entrypoints/workflow-list.test.tsx`, `workflow-list.layout.test.tsx`, `frontend/src/lib/workflowListContext.test.ts`, and the executions router tests exercise the behaviors above, notably: Progress split sort/filter affordances, percent-based sorting with blanks last, canonical Progress params and chips, mobile Progress controls, and no per-row step fetches; Provider Profile display of every selection state without current-default guesses, ID/state filtering and counting before pagination with authorization and system-workflow exclusions, rejection of invalid or conflicting parameters, preservation of query meaning across reloads, saved views, legacy runtime constraints, and list/detail return context, and partial or failed facets that keep selections instead of appearing empty.
