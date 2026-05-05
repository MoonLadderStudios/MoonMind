# Story Breakdown: Tasks List Page

- Source design: `docs/UI/TasksListPage.md`
- Original source document reference path: `docs/UI/TasksListPage.md`
- Story extraction date: 2026-05-05T01:00:58Z
- Requested output mode: jira
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

The design defines the desired Tasks List page as a task-oriented Mission Control table for user-visible Temporal-backed executions. It replaces detached top filters with Google Sheets-like column filters while preserving route hosting, URL shareability, pagination, live updates, mobile cards, accessibility, security boundaries, and old-link fail-safe behavior. System workflow browsing moves out of the normal page into a permission-gated diagnostics surface, and API/facet support must enforce task-scope authorization while enabling stable server-authoritative filtering.

## Coverage Points

- `DESIGN-REQ-001` **Task-oriented operator scanning surface** (requirement, 1. Purpose): The page must help operators inspect Temporal-backed MoonMind task executions with fast scanning, stable pagination, clear status visibility, accessible sorting, and column-level filtering.
- `DESIGN-REQ-002` **Related implementation and contract surfaces** (integration, 2. Related docs and implementation surfaces): Implementation must align the FastAPI dashboard routes, executions API, frontend entrypoint, shared CSS, and related canonical docs.
- `DESIGN-REQ-003` **Canonical route and server hosting** (requirement, 3. Route and hosting model): `/tasks/list` is canonical, legacy routes redirect, FastAPI hosts the shared Mission Control frontend, and runtime config arrives through the boot payload.
- `DESIGN-REQ-004` **Browser calls only MoonMind APIs** (security, 3. Route and hosting model; 18. Security and privacy): The browser must not call Temporal, GitHub, Jira, object storage, or runtime providers directly.
- `DESIGN-REQ-005` **Normal page is task-only, not a workflow browser** (constraint, 4. Product stance): Ordinary user-created task executions are the default surface; system workflows and manifest ingest rows do not belong in the normal task table.
- `DESIGN-REQ-006` **Current behavior and compatibility baseline** (migration, 5. Current page behavior): Existing control deck, URL parameters, API request shape, row model, desktop columns, mobile cards, loading, empty, error, and pagination behavior form the compatibility baseline.
- `DESIGN-REQ-007` **Desired layout replaces top filters** (requirement, 6. Desired page layout after column filters): Top filter dropdowns are removed after equivalent column filters exist; live updates, active chips, toolbar, desktop table, mobile sheet, and pagination remain in their proper surfaces.
- `DESIGN-REQ-008` **Default column model and exclusions** (requirement, 7. Column set in the desired design): The default table exposes task columns with specific sort/filter types while excluding Kind, Workflow Type, and Entry from the normal table.
- `DESIGN-REQ-009` **Admin diagnostics boundary** (security, 7.1 Admin diagnostics escape hatch): System and all-workflow browsing must move to a permission-gated diagnostics surface and must not be reachable by editing `/tasks/list` URL parameters.
- `DESIGN-REQ-010` **Compound header sort and filter controls** (requirement, 8. Column header interaction model): Each header has separate sort and filter targets, independent indicators, preserved aria-sort, and accessible filter state names.
- `DESIGN-REQ-011` **Sort behavior and limitations** (requirement, 8.1 Sort behavior): The initial sort remains scheduled descending; timestamp and non-timestamp defaults differ; only one primary sort is required and client-side current-page sorting must not be presented as global ordering.
- `DESIGN-REQ-012` **Filter popover structure and staging** (state-model, 9. Filter popover design; 9.1 Common popover structure): Filter popovers are anchored, keyboard-accessible panels with staged changes, search, checklist values, blanks, clear/cancel/apply, Only actions, counts, virtualization, and safe text rendering.
- `DESIGN-REQ-013` **Column-specific filter behavior** (requirement, 9.2-9.6 Status, Runtime, Repository, Date, and Text filter popovers): Status, runtime, repository, date, ID, and title filters each have specific value sources, display labels, raw values, blank handling, text behavior, and pagination reset rules.
- `DESIGN-REQ-014` **Include, exclude, AND, and OR semantics** (state-model, 10. Selection semantics): Filters use AND semantics across columns, OR semantics within a column, distinguish include and exclude modes, handle new live values predictably, and prevent or confirm empty impossible states.
- `DESIGN-REQ-015` **Active filter chips** (requirement, 11. Active filter chips): Every active filter has a product-labeled chip that can reopen or remove the filter; clear-all restores the default task-run view and mobile keeps chips visible.
- `DESIGN-REQ-016` **Shareable URL state and pagination reset** (state-model, 12. URL state in the desired design): The URL remains the shareable source of limit, pagination, sort, and active column filter state; filter changes reset pagination and previous-page cursor stacks.
- `DESIGN-REQ-017` **Old URL compatibility must fail safe** (migration, 12.1 Backward compatibility): Existing scope, workflowType, state, entry, and repo parameters must either map to task-list filters, redirect appropriately, or show recoverable messages without exposing system workflows.
- `DESIGN-REQ-018` **Canonical multi-value filter encoding** (protocol, 12.2 Canonical filter encoding): The desired URL/API encoding supports include, exclude, contains, exact, date bounds, blank filters, URL-encoded values, repeated params, and validation for contradictions.
- `DESIGN-REQ-019` **List API supports server-authoritative filtering** (integration, 13.1 List query requirements): The executions list API should support authoritative sort, include/exclude filters, text filters, date ranges, blanks, deterministic pagination, count modes, and task-scope authorization.
- `DESIGN-REQ-020` **Facet API supports filter popovers** (integration, 13.2 Facet query requirements): Facet data must be scoped, counted, paginated or searched as needed, resilient to failure, and must not include system-only values for the normal page.
- `DESIGN-REQ-021` **Live updates preserve staged filter choices** (state-model, 14. Live updates and filter stability): Polling can update rows when no popover is open, but open filter snapshots and unapplied user choices must remain stable.
- `DESIGN-REQ-022` **Accessibility parity** (requirement, 15. Accessibility requirements): Sort and filter controls are keyboard reachable, expose proper aria state, manage focus, support Escape/Enter behavior, label checkboxes and chips, and avoid color-only indicators.
- `DESIGN-REQ-023` **Mobile filter sheet parity** (requirement, 16. Mobile behavior): The mobile card layout remains primary, with a filter sheet exposing the same task-column filters, active chips, pagination reset, and no system workflow widening.
- `DESIGN-REQ-024` **Empty, error, and invalid-filter states** (observability, 17. Empty and error states after column filters): The page must keep current empty and later-page behavior, expose clear filters when relevant, show facet/list validation errors recoverably, and preserve editable user state.
- `DESIGN-REQ-025` **Security and privacy guardrails** (security, 18. Security and privacy): Facet/list values must follow authorization, URL state must exclude secrets, labels render as text, input sizes are bounded, and invalid filters return structured validation errors.
- `DESIGN-REQ-026` **Regression testing contract** (artifact, 19. Testing contract): Implementation must add or preserve tests across routing, removed controls, columns, header behavior, filters, chips, URL compatibility, mobile parity, system-scope safety, facet failure, live polling, and timestamp presentation.
- `DESIGN-REQ-027` **Explicit non-goals** (non-goal, 20. Non-goals): The design excludes spreadsheet editing, pivot tables, first-version multi-column sort, raw Temporal SQL, direct Temporal calls, saved views, replacing pagination/page size, removing live updates, and system browsing on the normal page.
- `DESIGN-REQ-028` **Sequenced migration to final desired state** (migration, 21. Desired implementation sequence): The desired sequence preserves current behavior, adds components behind flags, introduces state and API support, migrates filters, removes legacy top controls after parity tests, and reaches an operational spreadsheet-like final state.

## Ordered Story Candidates

### STORY-001: Tasks List canonical route and shell

- Short name: `tasks-list-shell`
- Source reference: `docs/UI/TasksListPage.md` (1. Purpose; 2. Related docs and implementation surfaces; 3. Route and hosting model; 5.1 Page shell)
- Description: As a MoonMind operator, I want `/tasks/list` to render the canonical Tasks List shell with redirected legacy routes and server-provided runtime configuration so I always land on the supported task list experience.
- Why: This establishes the page boundary and boot contract before changing table behavior.
- Independent test: Route tests and frontend render tests prove canonical and legacy routes resolve to the same Tasks List shell, boot payload config is present, and no non-MoonMind API endpoint is called by the page.
- Dependencies: None
- Needs clarification: None
- Acceptance criteria:
  - Given a request to `/tasks/list`, the response hosts the Mission Control React page with page key `tasks-list`.
  - Given requests to `/tasks` or `/tasks/tasks-list`, the server redirects to `/tasks/list`.
  - The rendered page contains one header control deck and one data slab using the wide data-panel layout.
  - Live updates state, polling copy, feature-disabled notice, and page-size/pagination surfaces remain available.
  - The frontend uses boot payload dashboard configuration and MoonMind API routes only.
- Requirements:
  - Preserve current shell behavior while making `/tasks/list` canonical.
  - Keep task-list route hosting inside FastAPI and the shared React/Vite frontend.
  - Keep current loading, polling, disabled, and data-panel surfaces available for later stories.
- Scope:
  - Serve `/tasks/list` through FastAPI with the `tasks-list` boot payload page key.
  - Redirect `/tasks` and `/tasks/tasks-list` to `/tasks/list`.
  - Render the header control deck, live updates toggle, polling copy, disabled-state notice, results/data slab, and wide data-panel layout.
  - Ensure the browser obtains runtime dashboard configuration from the MoonMind boot payload and calls only MoonMind APIs.
- Out of scope:
  - Column filter editors, facet APIs, and diagnostics browsing.
- Source design coverage:
  - `DESIGN-REQ-001`: Defines the operator scanning purpose for the shell.
  - `DESIGN-REQ-002`: Touches the named route, API, frontend entrypoint, and CSS surfaces.
  - `DESIGN-REQ-003`: Owns canonical route, redirects, hosting, boot payload, and layout.
  - `DESIGN-REQ-004`: Owns the frontend network boundary for the page.
  - `DESIGN-REQ-006`: Preserves current page-shell behavior as the baseline.

### STORY-002: Task-only visibility and diagnostics boundary

- Short name: `task-scope-diagnostics`
- Source reference: `docs/UI/TasksListPage.md` (4. Product stance; 7. Column set in the desired design; 7.1 Admin diagnostics escape hatch; 12.1 Backward compatibility; 18. Security and privacy)
- Description: As an ordinary Tasks List user, I want the page to always show user-visible task runs and never become a workflow-kind browser so system workflows remain confined to an explicit admin diagnostics surface.
- Why: This is the central product and security boundary for removing scope/workflow controls.
- Independent test: API/router and UI tests load old `scope`, `workflowType`, and `entry` URLs as ordinary and admin users and verify no system workflow rows, columns, filters, or facet counts appear on `/tasks/list`.
- Dependencies: STORY-001
- [NEEDS CLARIFICATION] Choose the exact behavior for authorized old system/all URLs: redirect to diagnostics or show an explanatory message.
- Acceptance criteria:
  - The normal list request is always bounded to task-run visibility regardless of editable URL parameters.
  - The default table exposes no Kind, Workflow Type, or Entry column.
  - System and all workflow scopes are ignored safely, redirected to diagnostics when authorized, or shown in a recoverable message without revealing rows.
  - Manifest ingest URLs do not add Workflow Type or Entry columns to the task table.
  - Diagnostics access, if linked, is permission-gated and visually separate from `/tasks/list`.
- Requirements:
  - Enforce task-oriented visibility at the backend authorization/query boundary.
  - Remove the ordinary workflow-kind browsing UX from Tasks List.
  - Fail safe for old URLs without losing task-list availability.
- Scope:
  - Bound the normal `/tasks/list` query to user-visible task executions equivalent to `scope=tasks`, `WorkflowType=MoonMind.Run`, and `mm_entry=run`.
  - Remove ordinary access to Scope, Workflow Type, Entry, Kind, and system/all workflow browsing from the normal page.
  - Handle old system/all/manifests URL parameters by failing safe, redirecting authorized users to diagnostics, or showing a recoverable message.
  - Keep diagnostics browsing permission-gated and outside the normal Tasks List table.
- Out of scope:
  - Building the full admin diagnostics UI unless the selected fail-safe behavior requires a minimal redirect target.
- Source design coverage:
  - `DESIGN-REQ-005`: Owns the task-only product stance.
  - `DESIGN-REQ-008`: Owns exclusion of Kind, Workflow Type, and Entry from the default table.
  - `DESIGN-REQ-009`: Owns diagnostics boundary and permission gate.
  - `DESIGN-REQ-017`: Owns old scope/workflow/entry fail-safe handling.
  - `DESIGN-REQ-025`: Owns security rules around unauthorized system visibility.

### STORY-003: Desktop columns and compound headers

- Short name: `desktop-column-headers`
- Source reference: `docs/UI/TasksListPage.md` (5.5 Current row model; 5.6 Current desktop table; 6. Desired page layout after column filters; 7. Column set in the desired design; 8. Column header interaction model; 8.1 Sort behavior; 20. Non-goals)
- Description: As an operator scanning tasks on desktop, I want each visible task column to own sorting and filtering controls so the table behaves like a compact operational spreadsheet.
- Why: This delivers the primary desktop interaction model without yet requiring every filter backend capability.
- Independent test: Frontend tests click header label and filter icon targets separately, assert default columns and excluded columns, verify aria-sort values, and confirm Started is absent.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Acceptance criteria:
  - The default desktop table includes the desired task columns and excludes Kind, Workflow Type, Entry, and Started.
  - Clicking a header label toggles sort without opening a filter popover.
  - Clicking a filter icon opens the matching filter popover without changing sort.
  - Sorted headers show direction and preserve `aria-sort` behavior.
  - The initial sort is `scheduledFor` descending with documented timestamp and string sort defaults.
  - Only one primary sort is required and multi-column sort controls are absent.
- Requirements:
  - Implement reusable sortable/filterable table-header controls.
  - Keep existing row display formatting and dependency summaries where still applicable.
  - Do not introduce excluded non-goal behaviors.
- Scope:
  - Render the desired default desktop columns: ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, Finished, with Integration optional.
  - Use compound headers with separate label sort target and funnel filter target.
  - Preserve status pill display precedence and omit Started timestamp.
  - Keep single-primary-sort behavior with scheduled descending as the initial sort.
  - Clearly indicate when sorting is current-page-only until server sorting is available.
- Out of scope:
  - Multi-column sorting, saved views, raw Temporal query UI, spreadsheet editing, and pivot tables.
- Source design coverage:
  - `DESIGN-REQ-006`: Preserves row model, current sort behavior, status pill precedence, and Started omission.
  - `DESIGN-REQ-007`: Owns moving sort/filter affordances into the table.
  - `DESIGN-REQ-008`: Owns desired default and optional column model.
  - `DESIGN-REQ-010`: Owns compound header behavior and indicators.
  - `DESIGN-REQ-011`: Owns sort defaults and single-sort limitation.
  - `DESIGN-REQ-027`: Owns non-goals for multi-sort and spreadsheet-like editing beyond filtering.

### STORY-004: Column filter popovers, chips, and selection semantics

- Short name: `column-filter-semantics`
- Source reference: `docs/UI/TasksListPage.md` (6. Desired page layout after column filters; 9. Filter popover design; 10. Selection semantics; 11. Active filter chips; 20. Non-goals)
- Description: As an operator, I want column filters with staged popover editing, include/exclude semantics, blanks, and active chips so I can refine task rows without detached top dropdowns.
- Why: This is the core user-visible column filtering behavior and replaces the current top filter form.
- Independent test: Frontend interaction tests stage filter changes, cancel without applying, apply include/exclude filters, remove individual chips, clear all filters, and verify top Scope/Workflow Type/Status/Entry/Repository controls are absent once parity exists.
- Dependencies: STORY-003
- [NEEDS CLARIFICATION] Decide whether Clear inside a popover applies immediately or stages a clear for Apply, then keep it consistent across columns.
- Acceptance criteria:
  - Checkbox and text changes remain staged until Apply.
  - Cancel, Escape, or outside click closes without applying staged changes.
  - Status filter uses canonical lifecycle order and maps to lifecycle state display precedence.
  - Runtime stores raw identifiers while displaying human-readable labels.
  - Repository supports value selection and existing exact text behavior.
  - Date filters support bounds and blanks where meaningful.
  - Deselecting `canceled` from all statuses creates an exclude filter represented as a chip such as `Status: not canceled`.
  - Every active filter has a clickable chip with a remove action, and Clear filters restores the default task-run view.
- Requirements:
  - Replace top Status and Repository controls with equivalent column filters.
  - Add Runtime and Skill column filters.
  - Keep values rendered as text and long lists bounded through virtualization, pagination, or server search.
  - Reset pagination when filters apply.
- Scope:
  - Build anchored keyboard-accessible filter popovers with title, summary, sort commands, search, select all, value checklist, blanks, clear, cancel, and apply.
  - Implement Status, Runtime, Repository, date, ID, and Title editor behavior using raw values and product labels.
  - Apply AND semantics across columns and OR semantics within a column.
  - Support include mode, exclude mode, blanks, Select all clear behavior, Only quick action, and safe empty-state handling.
  - Render active filter chips that reopen or remove a column filter and a Clear filters action that restores the default task-run view.
- Out of scope:
  - Saved filter views, raw Temporal Visibility SQL, and direct cell editing.
- Source design coverage:
  - `DESIGN-REQ-007`: Owns removing top filters after column filter parity.
  - `DESIGN-REQ-012`: Owns popover structure and staged behavior.
  - `DESIGN-REQ-013`: Owns column-specific filter editors.
  - `DESIGN-REQ-014`: Owns include/exclude and AND/OR semantics.
  - `DESIGN-REQ-015`: Owns active chips and clear-all behavior.
  - `DESIGN-REQ-027`: Keeps saved views and raw query authoring out of scope.

### STORY-005: Shareable filter URL compatibility and canonical encoding

- Short name: `filter-url-state`
- Source reference: `docs/UI/TasksListPage.md` (5.3 Current URL state; 12. URL state in the desired design; 12.1 Backward compatibility; 12.2 Canonical filter encoding)
- Description: As an operator sharing a Tasks List view, I want old and new URLs to load predictably and fail safe so links keep their task-focused meaning without exposing broader workflow scopes.
- Why: URL state is the durable contract between the UI, API query state, and old shared links.
- Independent test: URL round-trip tests load old single-value params, system/all params, manifest params, and new multi-value params, then assert active chips, canonical rewrites, validation errors, and pagination resets.
- Dependencies: STORY-002, STORY-004
- Needs clarification: None
- Acceptance criteria:
  - Existing `state=<value>` loads as a Status include filter.
  - Existing `repo=<value>` loads as a Repository exact include filter.
  - Existing `scope=system`, `scope=all`, system workflowType, and manifest entry links do not expose system or manifest rows in the normal page.
  - New include/exclude filters serialize to canonical params such as `stateNotIn` and `targetRuntimeIn`.
  - Contradictory include/exclude filters on the same field produce a clear validation error instead of ambiguous behavior.
  - Empty filter lists are normalized away.
  - Filter and page-size changes clear `nextPageToken` and the previous cursor stack.
- Requirements:
  - Keep URL state synchronized with `history.replaceState`.
  - Support comma-encoded and repeated-value representations where needed.
  - Use product labels in chips while preserving raw canonical values in URL/API state.
- Scope:
  - Parse current URL state for limit, nextPageToken, sort, sortDir, state, repo, scope, workflowType, and entry.
  - Map old `state` and `repo` params into equivalent Status and Repository column filters.
  - Fail safe for old scope, workflowType, and entry combinations according to the task-only boundary.
  - Write canonical include/exclude, contains, exact, date-bound, and blank filter params after users modify filters.
  - Reset `nextPageToken` and previous-page cursor stack on filter or page-size changes.
- Out of scope:
  - Persisting named saved views.
- Source design coverage:
  - `DESIGN-REQ-006`: Owns old URL baseline.
  - `DESIGN-REQ-016`: Owns shareable desired URL state and pagination reset.
  - `DESIGN-REQ-017`: Owns backward compatibility mapping and fail-safe handling.
  - `DESIGN-REQ-018`: Owns canonical filter encoding and validation.

### STORY-006: Executions list and facet API support for column filters

- Short name: `execution-filter-api`
- Source reference: `docs/UI/TasksListPage.md` (5.4 Current API request; 13. API and data requirements for column filtering; 18. Security and privacy)
- Description: As the Tasks List UI, I need server-authoritative list and facet APIs for multi-column filtering so results, counts, pagination, and popover values reflect the authorized task universe beyond the current page.
- Why: A Google Sheets-like filter experience requires backend support for stable, authorized result sets and facet counts.
- Independent test: FastAPI route/service tests exercise include/exclude filters, text/date/blank filters, sort, deterministic pagination, facet counts under current filters, auth scoping, invalid filter errors, and absence of system-only facet values.
- Dependencies: STORY-002, STORY-005
- [NEEDS CLARIFICATION] Confirm whether facet counts must be exact for the first version or may initially report estimated/unknown modes for expensive facets.
- Acceptance criteria:
  - List results are sorted and filtered by the server for supported canonical fields.
  - Pagination remains deterministic under active filters.
  - Count and countMode describe the fully filtered result when available.
  - Facet requests include all active filters except the requested facet by default.
  - Static facets such as Status can use frontend enums plus server counts, while dynamic facets come from server data.
  - Facet failure does not break the table and can fall back to current-page values with a visible notice.
  - System-only values and unauthorized values never appear in list rows, facets, or counts for `/tasks/list`.
- Requirements:
  - Use raw canonical values rather than display labels for API filters.
  - Reject contradictory filters clearly.
  - Bound text lengths and value-list sizes.
- Scope:
  - Extend `/api/executions` list queries for server-authoritative sort, include/exclude values, text filters, date ranges, blank filters, count/countMode, and deterministic pagination.
  - Add or expose a facet query surface such as `/api/executions/facets` that can return scoped facet values, labels, counts, blankCount, truncation, and pagination.
  - Apply current filters except the opened facet by default.
  - Keep all list and facet data subject to user authorization and task-only scope on the normal page.
  - Return structured validation errors for invalid filters and bounded input sizes.
- Out of scope:
  - Persistent storage changes unless existing query sources cannot support required counts or facets.
- Source design coverage:
  - `DESIGN-REQ-006`: Owns current API request baseline.
  - `DESIGN-REQ-019`: Owns list query requirements.
  - `DESIGN-REQ-020`: Owns facet query requirements and fallback expectations.
  - `DESIGN-REQ-025`: Owns authorization, validation, safe rendering inputs, and bounded values.

### STORY-007: Mobile, accessibility, and live-update stability

- Short name: `mobile-a11y-live`
- Source reference: `docs/UI/TasksListPage.md` (5.7 Current mobile cards; 14. Live updates and filter stability; 15. Accessibility requirements; 16. Mobile behavior)
- Description: As an operator on any device or assistive technology, I want equivalent filters, accessible controls, and stable staged selections while live updates run so the Tasks List remains usable during active monitoring.
- Why: The redesigned filtering model must work beyond the desktop table and must not be disrupted by polling.
- Independent test: Responsive and accessibility-focused tests verify mobile filter sheet parity, keyboard/focus behavior, aria names, chip removal names, live polling not mutating staged choices, and no system widening on cards.
- Dependencies: STORY-004, STORY-006
- Needs clarification: None
- Acceptance criteria:
  - Mobile users can reach status, runtime, skill, repository, title, ID, and date filters without the removed top dropdowns.
  - Mobile filter changes reset pagination just like desktop.
  - Focus moves into filter UI on open and returns to the originating control on close.
  - Escape cancels staged changes and Enter on Apply commits them.
  - Checkbox labels include value label and count when shown.
  - Color is not the only active sort/filter/status indicator.
  - When a popover or sheet editor is open, live updates do not overwrite staged selections.
- Requirements:
  - Keep desktop and mobile filter semantics equivalent.
  - Show subtle update notices when facet values change after a filter editor opens if implemented.
  - Keep system workflows unavailable from mobile task-card views.
- Scope:
  - Keep mobile cards as the primary narrow-screen presentation with title link, task ID, metadata, status pill, field grid, dependency summary, and View details action.
  - Add a mobile Filters button and sheet exposing the same filterable task columns as desktop.
  - Use the same filter editors adapted to full-screen or bottom-sheet layouts.
  - Preserve active chips on mobile through a scrollable row or compact filter summary.
  - Ensure sort/filter controls, popovers, checkboxes, chips, and sheets expose accessible names, focus management, keyboard behavior, and non-color indicators.
  - Freeze open filter snapshots and staged choices while live polling continues.
- Out of scope:
  - Mandatory contextual filter actions on card fields; those remain optional.
- Source design coverage:
  - `DESIGN-REQ-006`: Owns current mobile card behavior.
  - `DESIGN-REQ-021`: Owns live polling and staged-selection stability.
  - `DESIGN-REQ-022`: Owns accessibility requirements.
  - `DESIGN-REQ-023`: Owns mobile filter sheet parity and mobile task-only boundary.

### STORY-008: Empty/error states and regression coverage for final rollout

- Short name: `filter-rollout-tests`
- Source reference: `docs/UI/TasksListPage.md` (5.8 Current empty, loading, error, and pagination states; 17. Empty and error states after column filters; 19. Testing contract; 20. Non-goals; 21. Desired implementation sequence)
- Description: As a MoonMind maintainer, I want the final column-filter rollout covered by regression tests and recoverable empty/error states so the old filter form can be removed without losing current behavior.
- Why: This closes the migration by proving parity, failure handling, and non-goals before declaring the desired state complete.
- Independent test: Full unit/UI test runs demonstrate the testing contract passes and old top controls are absent while current empty/error/pagination behavior remains intact.
- Dependencies: STORY-003, STORY-004, STORY-005, STORY-006, STORY-007
- Needs clarification: None
- Acceptance criteria:
  - Loading state still shows `Loading tasks...`.
  - API errors render a visible error notice.
  - Empty first pages show `No tasks found for the current filters.` and include Clear filters when filters are active.
  - Empty later pages keep previous-page navigation available.
  - Facet request failures show an inline retry/fallback path without breaking the table.
  - Invalid filter parameters show structured errors and preserve user filter state for editing.
  - The old top Scope, Workflow Type, Status, Entry, and Repository controls are absent after column-filter parity.
  - The documented regression tests pass before rollout is considered complete.
- Requirements:
  - Treat TDD and regression evidence as required rollout gates.
  - Keep non-goals excluded from final UX.
  - Use the recommended implementation sequence without encoding a migration diary into canonical docs.
- Scope:
  - Preserve loading, error, empty first-page, empty later-page, result toolbar, page range, count, page size, and opaque cursor pagination behavior.
  - Show Clear filters when active filters cause an empty first page.
  - Show recoverable inline facet errors and list validation errors while preserving editable filter state.
  - Execute or add the testing contract across route rendering, removed top controls, default columns, header interactions, filters, chips, URL compatibility, mobile parity, system-scope safety, facet failure, live polling, empty later pages, and Started omission.
  - Remove old top Scope, Workflow Type, Status, Entry, and Repository controls after parity tests pass.
- Out of scope:
  - Saved named filter views, replacing page size/pagination controls, removing live updates, direct browser Temporal calls, raw Temporal SQL, multi-column sort, pivot tables, and cell editing.
- Source design coverage:
  - `DESIGN-REQ-006`: Owns current loading, empty, error, and pagination baseline.
  - `DESIGN-REQ-024`: Owns empty/error/invalid-filter states after column filters.
  - `DESIGN-REQ-026`: Owns the testing contract.
  - `DESIGN-REQ-027`: Owns final non-goal enforcement.
  - `DESIGN-REQ-028`: Owns migration sequencing and final desired state.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-002
- `DESIGN-REQ-006` -> STORY-001, STORY-003, STORY-005, STORY-006, STORY-007, STORY-008
- `DESIGN-REQ-007` -> STORY-003, STORY-004
- `DESIGN-REQ-008` -> STORY-002, STORY-003
- `DESIGN-REQ-009` -> STORY-002
- `DESIGN-REQ-010` -> STORY-003
- `DESIGN-REQ-011` -> STORY-003
- `DESIGN-REQ-012` -> STORY-004
- `DESIGN-REQ-013` -> STORY-004
- `DESIGN-REQ-014` -> STORY-004
- `DESIGN-REQ-015` -> STORY-004
- `DESIGN-REQ-016` -> STORY-005
- `DESIGN-REQ-017` -> STORY-002, STORY-005
- `DESIGN-REQ-018` -> STORY-005
- `DESIGN-REQ-019` -> STORY-006
- `DESIGN-REQ-020` -> STORY-006
- `DESIGN-REQ-021` -> STORY-007
- `DESIGN-REQ-022` -> STORY-007
- `DESIGN-REQ-023` -> STORY-007
- `DESIGN-REQ-024` -> STORY-008
- `DESIGN-REQ-025` -> STORY-002, STORY-006
- `DESIGN-REQ-026` -> STORY-008
- `DESIGN-REQ-027` -> STORY-003, STORY-004, STORY-008
- `DESIGN-REQ-028` -> STORY-008

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-003
- `STORY-005` depends on: STORY-002, STORY-004
- `STORY-006` depends on: STORY-002, STORY-005
- `STORY-007` depends on: STORY-004, STORY-006
- `STORY-008` depends on: STORY-003, STORY-004, STORY-005, STORY-006, STORY-007

## Out-of-Scope Items and Rationale

- Spreadsheet-style cell editing, arbitrary pivot tables, raw Temporal Visibility SQL, direct browser calls to Temporal, and saved named views are excluded because the design is a compact operational task table rather than a generic spreadsheet or Temporal namespace browser.
- Multi-column sort is excluded from the first version because the design requires only one primary sort and prioritizes stable filtering and pagination.
- Page size, pagination controls, and the Live updates toggle remain because they are result-window and page-behavior controls, not column filters.
- System workflow browsing remains outside `/tasks/list` because it belongs in a permission-gated diagnostics surface.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
