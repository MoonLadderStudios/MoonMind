# Research: Mobile, Accessibility, and Live-Update Stability

## FR-001 / DESIGN-REQ-023 Mobile Filter Reachability

Decision: Complete the existing mobile control set by adding ID and Title text filters to the shared column-filter model.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` already exposed mobile Status, Runtime, Skill, Repository, Scheduled, Created, and Finished controls; `docs/UI/TasksListPage.md` section 16 requires mobile access to status, runtime, skill, repository, title, ID, and date filters.
Rationale: Extending the existing filter model keeps desktop and mobile semantics aligned and avoids reintroducing removed top dropdowns.
Alternatives considered: Add a separate mobile-only filter sheet state. Rejected because it would duplicate semantics already present in the column-filter model.
Test implications: UI unit test must assert mobile ID and Title controls exist and submit canonical query params.

## FR-002 Pagination Reset

Decision: Continue routing mobile filter changes through `applyFilters()`, which resets `listCursor` and `cursorStack`.
Evidence: Existing code uses `applyFilters()` for mobile controls and existing pagination/filter tests cover cursor reset behavior.
Rationale: Reusing the existing path preserves desktop/mobile parity.
Alternatives considered: Add a separate mobile reset helper. Rejected as unnecessary duplication.
Test implications: Extended mobile filter URL assertion must omit `nextPageToken` after filter changes.

## FR-004 / FR-006 / DESIGN-REQ-022 Focus and Keyboard Dialog Behavior

Decision: Track the originating filter button, focus the first dialog control after opening, return focus on close, and treat Enter as Apply for non-textarea targets.
Evidence: Existing dialog supported Escape/cancel/apply but did not move focus into the dialog or return focus.
Rationale: This directly satisfies the source accessibility requirements with small component-local state.
Alternatives considered: Introduce a shared dialog component. Rejected because this page already owns a compact popover and no broader abstraction is required.
Test implications: Add UI test for focus-in, Enter apply, URL update, and focus return.

## FR-007 / DESIGN-REQ-021 Live-Update Stability

Decision: Disable the execution-list refetch interval while a desktop filter editor is open.
Evidence: `useQuery` used `refetchInterval` whenever live updates were enabled; source design says live updates must not overwrite staged choices while an editor is open.
Rationale: Pausing the interval while the editor is open is deterministic and avoids changing active filters or option lists underneath the staged editor.
Alternatives considered: Snapshot every facet and current-page value at open time while continuing polling. Rejected as higher complexity for the same user-visible guarantee.
Test implications: Existing staging tests plus code inspection cover the guard; final UI tests ensure staging behavior remains intact.

## FR-009 Task-Only Visibility

Decision: Preserve existing task-only scope enforcement and workflow-kind compatibility handling.
Evidence: Existing tests cover absence of Scope, Workflow Type, Entry, and Kind controls and verify legacy workflow-scope URLs normalize to task-only visibility.
Rationale: The source brief explicitly says system workflows remain unavailable from mobile task-card views.
Alternatives considered: Add mobile diagnostics access. Rejected as out of scope and contrary to ordinary task-card visibility.
Test implications: Existing UI tests remain final validation evidence.
