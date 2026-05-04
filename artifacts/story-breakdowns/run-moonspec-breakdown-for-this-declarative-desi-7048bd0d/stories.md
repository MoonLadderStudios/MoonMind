# Story Breakdown: Tasks List Page

- Source design: `docs/UI/TasksListPage.md`
- Original source document reference path: `docs/UI/TasksListPage.md`
- Story extraction date: `2026-05-04T23:57:38Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines the desired-state Mission Control Tasks List page as a task-oriented, FastAPI-hosted React surface at /tasks/list. It replaces detached top filters with spreadsheet-like column filters while preserving stable pagination, shareable URL state, live polling, and accessible sorting. The normal page must remain bounded to user-visible task executions, with system and all-workflow browsing moved to a permission-gated diagnostics surface. The contract also defines canonical filter encoding, API list/facet requirements, live-update stability, mobile parity, security constraints, testing expectations, and explicit non-goals.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Task-oriented execution scanning surface** — The page helps operators inspect Temporal-backed MoonMind task executions with fast scanning, stable pagination, status visibility, accessible sorting, and Google Sheets-like column filters. Source: 1. Purpose.
- `DESIGN-REQ-002` (integration): **Use related architecture and API contracts without direct external calls** — The page uses existing API, Temporal visibility, Mission Control architecture, and design-system contracts, and the browser calls only MoonMind APIs. Source: 2. Related docs and implementation surfaces; 3. Route and hosting model.
- `DESIGN-REQ-003` (requirement): **Canonical Tasks List route and hosting model** — /tasks/list is canonical, /tasks and /tasks/tasks-list redirect to it, FastAPI server-hosts the page, emits the tasks-list boot key, uses a wide data-panel layout, and passes dashboard config in the boot payload. Source: 3. Route and hosting model.
- `DESIGN-REQ-004` (security): **Normal list is task-run focused and excludes system browsing** — The default view shows ordinary user-created task executions, hides system workflows, does not expose Kind/Workflow Type/Entry columns, and remains scoped to task runs. Source: 4. Product stance; 7. Column set in the desired design.
- `DESIGN-REQ-005` (migration): **Preserve current behavior while migrating** — Existing shell, polling, list-disabled behavior, row fields, pagination, empty/error states, status options, and intentional omissions such as Started timestamp remain preserved unless explicitly replaced. Source: 5. Current page behavior; 20. Non-goals; 21. Desired implementation sequence.
- `DESIGN-REQ-006` (requirement): **Column-filter layout replaces top filter controls** — The page removes Scope, Workflow Type, Status, Entry, and Repository top controls once equivalent column filters exist, while keeping header behavior, active chips, results toolbar, pagination, page size, and live updates. Source: 6. Desired page layout after column filters.
- `DESIGN-REQ-007` (state-model): **Default column model and mobile parity** — The default table exposes ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished with optional Integration, while mobile exposes equivalent filterable columns through a filter sheet. Source: 7. Column set in the desired design; 16. Mobile behavior.
- `DESIGN-REQ-008` (security): **Admin diagnostics escape hatch for broad workflow visibility** — System/all-workflow browsing belongs in a permission-gated diagnostics surface and old broad-scope URLs cannot widen /tasks/list visibility for ordinary users. Source: 7.1 Admin diagnostics escape hatch.
- `DESIGN-REQ-009` (requirement): **Compound sortable and filterable headers** — Each header has separate sort-label and filter-icon targets with independent indicators, accessible names, preserved aria-sort behavior, and no accidental clearing between sorting and filtering. Source: 8. Column header interaction model.
- `DESIGN-REQ-010` (requirement): **Deterministic sort behavior with server-authoritative direction** — Initial sort is scheduledFor descending; timestamp columns default descending, other columns ascending, current sort toggles direction, only one primary sort is required, and current-page sort must not be misrepresented as global when the API lacks server sort. Source: 8.1 Sort behavior.
- `DESIGN-REQ-011` (requirement): **Keyboard-accessible staged filter popovers** — Filter popovers are anchored accessible panels with title, summary, sort commands, search, select all, checklist, blanks, clear/cancel/apply, staged changes, optional Only action, virtualization/pagination for long lists, and text-safe labels. Source: 9. Filter popover design; 9.1 Common popover structure.
- `DESIGN-REQ-012` (requirement): **Specialized column filter editors** — Status, Runtime, Repository, date, ID, and Title filters each have specific raw values, display labels, blank handling, text/date semantics, canonical status order, and pagination-reset behavior. Source: 9.2-9.6 Status, Runtime, Repository, Date, and Text filter popovers.
- `DESIGN-REQ-013` (state-model): **AND across columns, OR within columns, include/exclude modes** — Column filters use AND semantics across columns, OR within a column, support include and exclude modes, allow new live values according to mode, clear on Select all, and handle all-values deselected safely. Source: 10. Selection semantics.
- `DESIGN-REQ-014` (requirement): **Active filter chips summarize and control filters** — Every active column filter has a product-labeled chip that reopens the filter, clears only its column through a remove action, and remains usable on mobile. Source: 11. Active filter chips.
- `DESIGN-REQ-015` (integration): **Shareable URL state and fail-safe compatibility mapping** — URL state stores pagination, sort, and column filters; old scope/workflowType/state/entry/repo links map, redirect, or explain safely; canonical include/exclude/text/date/blank filter params support shareable reloads. Source: 12. URL state in the desired design; 12.1 Backward compatibility; 12.2 Canonical filter encoding.
- `DESIGN-REQ-016` (integration): **Execution list API supports authoritative filtering and pagination** — /api/executions should support server sort, multi-value include/exclude, text filters, date ranges, blank/null filters, deterministic pagination, filtered counts, normalized values, access control, and task-bounded normal queries. Source: 13.1 List query requirements.
- `DESIGN-REQ-017` (integration): **Facet API supports filter popover values and counts** — Facet requests return scoped values, labels, counts, blank counts, count mode, truncation, optional pagination/search, current-context counts, static status support, dynamic facet support, graceful fallback, and no system-only leakage. Source: 13.2 Facet query requirements.
- `DESIGN-REQ-018` (requirement): **Live updates do not disturb staged filtering** — Polling may refetch when no popover is open, but open popovers keep stable checklist snapshots, staged choices are not overwritten, and include/exclude behavior governs new matching values. Source: 14. Live updates and filter stability.
- `DESIGN-REQ-019` (constraint): **Accessible sort, filter, chip, and mobile controls** — Sort and filter targets are keyboard reachable, expose correct ARIA state, manage focus on popover open/close, support Escape and Enter behavior, label checkboxes and chip removes, and avoid color-only state indicators. Source: 15. Accessibility requirements.
- `DESIGN-REQ-020` (requirement): **Mobile card filtering remains equivalent to desktop** — Mobile retains cards, adds a Filters button and sheet, exposes the same filterable columns, shows active chips, resets pagination on filter changes, and cannot reveal system workflows. Source: 16. Mobile behavior.
- `DESIGN-REQ-021` (observability): **Recoverable empty, error, facet, and old URL states** — No-match first pages show the standard empty message and Clear filters; facet/list errors preserve editable state; unsupported old URLs are mapped, redirected, or made recoverable; later empty pages keep previous navigation. Source: 17. Empty and error states after column filters.
- `DESIGN-REQ-022` (security): **Security and privacy guardrails for filter data** — Only MoonMind APIs are called; facets and list rows are authorization scoped; URL state has no secrets; labels render as text; filter lengths and value lists are bounded; validation errors are structured; system visibility remains backend-authorized. Source: 18. Security and privacy.
- `DESIGN-REQ-023` (constraint): **Behavioral testing contract for page, filters, URLs, facets, live updates, and omissions** — Implementation must preserve/add tests for route rendering, URL compatibility, removed top controls, absent Kind/Workflow Type/Entry columns, header interactions, accessibility state, filters, chips, pagination reset, mobile parity, facet failure, live polling stability, later-page empty navigation, and Started omission. Source: 19. Testing contract.
- `DESIGN-REQ-024` (non-goal): **Explicit non-goals keep scope bounded** — The design excludes spreadsheet cell editing, pivot tables, first-version multi-column sort, raw Temporal SQL, direct Temporal browser calls, first-version saved views, replacing page size/pagination, removing live updates, and exposing system browsing through the normal list. Source: 20. Non-goals.
- `DESIGN-REQ-025` (migration): **Safe implementation sequence without turning docs into rollout backlog** — The desired product sequence preserves current behavior, introduces reusable header filters behind a flag, maps state/chips/URLs, adds API filters/facets, migrates Status/Repository/Runtime/Skill, removes old top controls after parity tests, and keeps compatibility parsing until a documented window ends. Source: 21. Desired implementation sequence.

## Ordered Story Candidates

### STORY-001: Canonical task-run list route with fail-safe workflow visibility

- Short name: `canonical-task-route`
- Source reference path: `docs/UI/TasksListPage.md`
- Source sections: 3. Route and hosting model, 4. Product stance, 7. Column set in the desired design, 7.1 Admin diagnostics escape hatch, 12.1 Backward compatibility, 18. Security and privacy
- Dependencies: None
- Independent test: Load /tasks, /tasks/tasks-list, /tasks/list, and old broad-scope URLs as an ordinary user; assert the boot payload renders tasks-list, the visible rows and requests remain task-run scoped, and system/all/manifest scopes are ignored, redirected, or explained without revealing system workflows.
- Needs clarification: None

Why this story exists:

As a Mission Control operator, I want /tasks/list to always load the task-oriented execution list and keep broad workflow browsing out of the ordinary page so that shared links and manual URL edits cannot expose system workflow rows.

Acceptance criteria:

- /tasks/list is the canonical route and /tasks plus /tasks/tasks-list redirect to it.
- The server renders the tasks-list page key, wide data-panel layout configuration, and runtime dashboard config through the boot payload.
- The browser calls MoonMind APIs only and never calls Temporal, GitHub, Jira, object storage, or runtime providers directly.
- The normal page always requests user-visible task runs, equivalent to scope=tasks / WorkflowType=MoonMind.Run / mm_entry=run in current API terms.
- System workflows, provider-profile managers, internal monitors, maintenance workflows, and manifest-ingest workflows do not appear in the ordinary task table.
- Old scope=system, scope=all, system workflowType, and entry=manifest URLs fail safe by preserving task-run visibility, redirecting authorized admins to diagnostics, redirecting manifest links to the Manifests page, or showing a recoverable message.
- No Kind, Workflow Type, or Entry column is introduced to make broad workflow browsing available from /tasks/list.

Owned coverage:

- `DESIGN-REQ-002`: Owns MoonMind-only browser API calls and related surface boundaries for the route.
- `DESIGN-REQ-003`: Owns canonical route, redirects, server hosting, boot payload, dashboard config, and wide layout.
- `DESIGN-REQ-004`: Owns task-run default scope and removal of normal workflow-kind browsing.
- `DESIGN-REQ-008`: Owns diagnostics escape-hatch routing and permission gate expectations.
- `DESIGN-REQ-015`: Owns old scope/workflowType/entry fail-safe handling for visibility-sensitive URLs.
- `DESIGN-REQ-022`: Owns backend authorization as the final authority for visibility.

### STORY-002: Column-filtered Tasks List layout with active chips and mobile sheet

- Short name: `column-filter-layout`
- Source reference path: `docs/UI/TasksListPage.md`
- Source sections: 5. Current page behavior, 6. Desired page layout after column filters, 7. Column set in the desired design, 11. Active filter chips, 16. Mobile behavior, 20. Non-goals
- Dependencies: STORY-001
- Independent test: Render the Tasks List page at desktop and mobile widths with column filters enabled; assert the old top filters are absent, the default columns and cards are present, active filters appear as actionable chips, mobile exposes the same filterable columns through a sheet, and page size, pagination, live updates, and omitted non-goals remain unchanged.
- Needs clarification: None

Why this story exists:

As an operator scanning task executions, I want filters to live on the task columns, with clear active filter chips and equivalent mobile controls, so that the page behaves like a compact operational spreadsheet instead of a form above a table.

Acceptance criteria:

- The control deck contains page title, Live updates toggle, polling copy, and the feature-disabled notice when applicable.
- Scope, Workflow Type, Status, Entry, and Repository top controls are absent after column filters are enabled.
- Active filter chips and Clear filters are shown in an active query row when filters are active.
- The results toolbar retains page summary, page size, pagination, and optional column visibility controls.
- Default desktop columns are ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished; Integration may be optional.
- The default table does not include Kind, Workflow Type, Entry, or Started columns.
- Clicking a filter chip opens its column filter and the chip remove action clears only that column filter.
- Clear filters clears all column filters and restores the default task-run view without removing page-size or live-update behavior.
- Mobile keeps the card layout, shows active chips, and exposes ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, Finished, and optional Integration filters through a mobile filter sheet.

Owned coverage:

- `DESIGN-REQ-001`: Owns the operator scanning surface and spreadsheet-like column-filter presentation.
- `DESIGN-REQ-005`: Owns preservation of current shell/card/pagination behavior and the Started omission.
- `DESIGN-REQ-006`: Owns removal of detached filters and placement of chips, toolbar, table, and mobile sheet.
- `DESIGN-REQ-007`: Owns default column set and optional Integration handling.
- `DESIGN-REQ-014`: Owns active chip display, reopen, remove, and mobile visibility behavior.
- `DESIGN-REQ-020`: Owns mobile filter-sheet parity and card layout continuity.
- `DESIGN-REQ-024`: Owns exclusion of cell editing, pivots, saved views, pagination replacement, live-update removal, and normal system browsing.

### STORY-003: Accessible sortable headers and staged column filter popovers

- Short name: `filter-popover-semantics`
- Source reference path: `docs/UI/TasksListPage.md`
- Source sections: 8. Column header interaction model, 8.1 Sort behavior, 9. Filter popover design, 9.1 Common popover structure, 9.2 Status filter popover, 9.3 Runtime filter popover, 9.4 Repository filter popover, 9.5 Date filter popovers, 9.6 Text filter popovers, 10. Selection semantics, 15. Accessibility requirements
- Dependencies: STORY-002
- Independent test: Use keyboard and pointer interactions against representative headers and popovers; assert label clicks toggle sort only, filter icons open popovers only, staged checklist/text/date changes apply or cancel correctly, include/exclude modes encode the expected state, focus and ARIA behavior match the contract, and unsafe labels render as text.
- Needs clarification: None

Why this story exists:

As a keyboard and pointer user, I want each column header to separate sorting from filtering and each popover to stage changes predictably, so that sorting, value selection, include/exclude filtering, and specialized text/date filters are reliable and accessible.

Acceptance criteria:

- Each sortable/filterable header exposes a sort label target and a separate filter icon target.
- Activating the label toggles sort without opening the popover; activating the icon opens the correct popover without changing sort.
- The initial sort is scheduledFor descending; unsorted timestamp columns default descending, unsorted non-timestamp columns default ascending, and the current column toggles asc/desc.
- Sorted and filtered columns show distinct indicators, preserve aria-sort behavior, and expose descriptive filter button state.
- Filter popovers stage checkbox/text/date changes until Apply; Cancel, Escape, and outside click close without applying staged changes.
- Clear behavior is consistent across columns and either applies immediately or stages a clear state for Apply confirmation.
- Status uses canonical lifecycle order and filters by MoonMind lifecycle state using rawState || state || status display precedence.
- Runtime stores raw identifiers while displaying human-readable labels and supports blanks.
- Repository supports value checklist search, optional contains/exact text mode, old exact repo mapping, and blanks.
- Scheduled, Created, and Finished support sort commands, inclusive From/To bounds, optional relative presets, and blank handling where meaningful.
- ID supports exact and contains matching; Title supports contains matching; applied text filters trim query whitespace and reset pagination.
- Filters use AND semantics across columns, OR semantics within a column, and distinguish include mode from exclude mode.
- Deselecting one value from all-selected status creates an exclude filter such as not canceled, while selecting specific runtime values creates an include filter.
- Focus moves into popovers and returns to the filter button, Escape cancels, Enter on Apply applies, checkbox labels include labels/counts, and color is not the only state indicator.

Owned coverage:

- `DESIGN-REQ-009`: Owns compound header interaction and indicators.
- `DESIGN-REQ-010`: Owns sort defaults, toggling, single primary sort, and truthful global/current-page sort presentation.
- `DESIGN-REQ-011`: Owns popover structure, staging, clear/cancel/apply, search, Only action, virtualization/pagination, and safe text rendering.
- `DESIGN-REQ-012`: Owns specialized status, runtime, repository, date, ID, and title filter behavior.
- `DESIGN-REQ-013`: Owns AND/OR, include/exclude, Select all, new live values, and all-values deselected semantics.
- `DESIGN-REQ-019`: Owns sort/filter/chip/mobile accessibility behavior tied to the editor controls.

### STORY-004: Canonical URL, list API, and facet contracts for task column filters

- Short name: `filter-api-url-contract`
- Source reference path: `docs/UI/TasksListPage.md`
- Source sections: 12. URL state in the desired design, 12.1 Backward compatibility, 12.2 Canonical filter encoding, 13. API and data requirements for column filtering, 13.1 List query requirements, 13.2 Facet query requirements, 18. Security and privacy
- Dependencies: STORY-001, STORY-003
- Independent test: Exercise canonical and old query strings plus API request construction with mocked list and facet responses; assert canonical include/exclude/text/date/blank params round-trip through URL state, contradictory filters fail with structured validation errors, list requests use server-authoritative filtering/sort when available, and facet values/counts are scoped by current filters and authorization.
- Needs clarification: None

Why this story exists:

As an operator sharing and reloading filtered task lists, I want URL parameters, list queries, and facet values to represent column filters canonically so that sorting, pagination, counts, and filter choices are deterministic beyond the current page.

Acceptance criteria:

- URL state includes limit, nextPageToken, sort, sortDir, and active column filter params.
- Canonical filter params support stateIn/stateNotIn, targetRuntimeIn/NotIn, targetSkillIn/NotIn, repoIn/NotIn, repoContains, integrationIn/NotIn, taskId/taskIdContains, titleContains, scheduledFrom/To, createdFrom/To, closedFrom/To, and meaningful blank flags.
- Comma-separated values are URL-encoded and repeated parameters are supported for values that may contain commas.
- The browser normalizes empty lists away and never sends no-op filters.
- The API rejects contradictory include and exclude filters on the same field with a clear validation error.
- Filter changes reset nextPageToken and the previous-page cursor stack.
- List requests support server-authoritative sort, include/exclude filters, text filters, date ranges, blanks, deterministic pagination, and filtered count/countMode.
- Display labels are never sent as canonical filter values when raw values exist.
- Facet requests include all active filters except the opened facet, return scoped values, labels, counts, blankCount, countMode, truncation state, and optional pagination token.
- Static status facets may come from frontend enums plus server counts; dynamic Runtime, Skill, Repository, and Integration facets come from server data.
- Facet failure does not break the table and can fall back to current-page values with a visible current-page-only notice.
- List and facet APIs remain authorization scoped and never expose hidden/system-only values or counts on /tasks/list.

Owned coverage:

- `DESIGN-REQ-015`: Owns URL state, canonical params, old state/repo compatibility, canonical rewrites, and pagination reset.
- `DESIGN-REQ-016`: Owns list API filtering, sorting, pagination, counts, normalized values, and access-control authority.
- `DESIGN-REQ-017`: Owns facet endpoint shape, scoping, counts, fallback, pagination/search, and no system leakage.
- `DESIGN-REQ-022`: Owns security and privacy for URL/API filter values, labels, bounds, validation errors, and authorization.

### STORY-005: Live update stability, recoverable states, and regression coverage for filtered lists

- Short name: `live-state-verification`
- Source reference path: `docs/UI/TasksListPage.md`
- Source sections: 5.8 Current empty, loading, error, and pagination states, 14. Live updates and filter stability, 17. Empty and error states after column filters, 19. Testing contract, 21. Desired implementation sequence
- Dependencies: STORY-002, STORY-003, STORY-004
- Independent test: Run focused frontend/API tests around polling and state handling: open a popover and stage changes while polling data/facets change, trigger empty first and later pages, force facet/list validation errors, load old URLs, and assert the testing contract behaviors remain covered before downstream implementation is considered complete.
- Needs clarification: None

Why this story exists:

As an operator relying on a live task table, I want polling, empty states, errors, and tests to protect staged filter work and compatibility behavior so that the filtered list remains trustworthy under refreshes, API failures, and old shared links.

Acceptance criteria:

- When no filter popover is open, live updates may refetch according to the configured polling interval.
- When a filter popover is open, the checklist snapshot remains stable until close or explicit refresh, and staged selections are not overwritten.
- New matching rows can appear and no-longer-matching rows can disappear after polling according to active filters.
- Include-mode filters do not automatically include newly discovered values; exclude-mode filters include newly discovered values unless excluded.
- Facet changes while a popover is open may show subtle copy such as Values updated after you opened this filter.
- First-page no-match results show No tasks found for the current filters. and include Clear filters when filters are active.
- Later empty pages keep previous-page navigation available.
- Facet errors show inline warning and retry inside the popover; list filter validation errors preserve editable filter state.
- Unsupported old URL combinations map, redirect, or show recoverable Clear filters behavior without revealing system workflows.
- Regression tests cover route rendering, URL compatibility, removed top controls, absent Kind/Workflow Type/Entry columns, header sort/filter separation, aria-sort, active filter accessible names, canonical status order, raw/runtime label mapping, include/exclude examples, pagination reset, chip clearing, mobile parity, diagnostics-only system scopes, facet failure, live polling stability, later-page empty navigation, and Started omission.
- The desired implementation sequence preserves existing behavior first, adds reusable components and URL/API support behind controlled rollout, removes old controls after parity tests, and keeps compatibility parsing for the documented window.

Owned coverage:

- `DESIGN-REQ-005`: Owns current loading/error/empty/pagination behavior and preservation of old useful behavior during migration.
- `DESIGN-REQ-018`: Owns polling and staged filter stability under live updates.
- `DESIGN-REQ-021`: Owns empty, facet error, list validation error, unsupported old URL, and later-page empty recovery.
- `DESIGN-REQ-023`: Owns the explicit behavioral test contract for route, filters, URLs, facets, live updates, mobile, and omissions.
- `DESIGN-REQ-025`: Owns safe sequencing and parity-test expectation without writing rollout checklists into canonical docs.

## Coverage Matrix

- `DESIGN-REQ-001` → STORY-002
- `DESIGN-REQ-002` → STORY-001
- `DESIGN-REQ-003` → STORY-001
- `DESIGN-REQ-004` → STORY-001
- `DESIGN-REQ-005` → STORY-002, STORY-005
- `DESIGN-REQ-006` → STORY-002
- `DESIGN-REQ-007` → STORY-002
- `DESIGN-REQ-008` → STORY-001
- `DESIGN-REQ-009` → STORY-003
- `DESIGN-REQ-010` → STORY-003
- `DESIGN-REQ-011` → STORY-003
- `DESIGN-REQ-012` → STORY-003
- `DESIGN-REQ-013` → STORY-003
- `DESIGN-REQ-014` → STORY-002
- `DESIGN-REQ-015` → STORY-001, STORY-004
- `DESIGN-REQ-016` → STORY-004
- `DESIGN-REQ-017` → STORY-004
- `DESIGN-REQ-018` → STORY-005
- `DESIGN-REQ-019` → STORY-003
- `DESIGN-REQ-020` → STORY-002
- `DESIGN-REQ-021` → STORY-005
- `DESIGN-REQ-022` → STORY-001, STORY-004
- `DESIGN-REQ-023` → STORY-005
- `DESIGN-REQ-024` → STORY-002
- `DESIGN-REQ-025` → STORY-005

## Dependencies

- `STORY-001` depends on no prior stories.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-002.
- `STORY-004` depends on STORY-001, STORY-003.
- `STORY-005` depends on STORY-002, STORY-003, STORY-004.

## Out-of-Scope Items and Rationale

- Spreadsheet-style cell editing. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Arbitrary pivot tables. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Multi-column sort in the first version. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- User-authored raw Temporal Visibility SQL. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Direct browser calls to Temporal or other non-MoonMind services. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Saving named filter views in the first version. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Replacing page-size or pagination controls. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Removing the Live updates toggle. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.
- Exposing system workflow browsing through the normal Tasks List page. Rationale: explicitly listed as a non-goal in `docs/UI/TasksListPage.md` and therefore should not become part of any first-pass story acceptance criteria.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
