# Research: Mobile, Accessibility, and Live-Update Stability

## FR-001 / DESIGN-REQ-023 Mobile Filter Reachability

Decision: Implemented and verified. The mobile control set now includes ID and Title text filters through the shared column-filter model.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` defines `taskId` and `title` text filters in the active filter fields, parsing, serialization, summaries, and mobile controls. `frontend/src/entrypoints/tasks-list.test.tsx` asserts `Mobile ID filter value` and `Mobile Title filter value`, changes both controls, and verifies canonical `taskIdContains` and `titleContains` query params.
Rationale: Extending the existing filter model keeps desktop and mobile semantics aligned and avoids reintroducing removed top dropdowns.
Alternatives considered: Add a separate mobile-only filter sheet state. Rejected because it would duplicate semantics already present in the column-filter model.
Test implications: Final UI validation keeps the focused Tasks List Vitest coverage and full unit runner.

## FR-002 Pagination Reset

Decision: Implemented and verified. Mobile filter changes continue routing through `applyFilters()`, which resets `listCursor` and `cursorStack`.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` uses the shared mobile filter application path. `frontend/src/entrypoints/tasks-list.test.tsx` verifies the post-filter request URL is task-scoped and omits stale pagination cursor state while including the new ID and Title filters.
Rationale: Reusing the existing path preserves desktop/mobile parity.
Alternatives considered: Add a separate mobile reset helper. Rejected as unnecessary duplication.
Test implications: Final UI validation through the focused Tasks List Vitest test and `./tools/test_unit.sh`.

## FR-004 / FR-006 / DESIGN-REQ-022 Focus and Keyboard Dialog Behavior

Decision: Implemented and verified. The page tracks the originating filter button, focuses the first dialog control after opening, returns focus through the close helper, and treats Enter as Apply for non-textarea targets.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` includes `pendingFocusField`, `filterTriggerRef`, dialog focus handling, and an Enter key path that applies staged edits. `frontend/src/entrypoints/tasks-list.test.tsx` verifies the Title filter dialog receives focus and Enter applies the staged text filter to the request URL.
Rationale: This directly satisfies the source accessibility requirements with small component-local state.
Alternatives considered: Introduce a shared dialog component. Rejected because this page already owns a compact popover and no broader abstraction is required.
Test implications: Final UI validation through the focused Tasks List Vitest test and full unit runner.

## FR-007 / DESIGN-REQ-021 Live-Update Stability

Decision: Implemented and verified by code inspection plus staging tests. The execution-list refetch interval is disabled while a desktop filter editor is open.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` sets `refetchInterval` only when live updates are enabled, the list is enabled, and no filter editor is open. Existing staging tests continue to cover draft preservation and dismissal behavior.
Rationale: Pausing the interval while the editor is open is deterministic and avoids changing active filters or option lists underneath the staged editor.
Alternatives considered: Snapshot every facet and current-page value at open time while continuing polling. Rejected as higher complexity for the same user-visible guarantee.
Test implications: Final UI validation combines focused Tasks List Vitest coverage with code inspection for the polling guard.

## FR-009 Task-Only Visibility

Decision: Preserve existing task-only scope enforcement and workflow-kind compatibility handling.
Evidence: Existing tests cover absence of Scope, Workflow Type, Entry, and Kind controls and verify legacy workflow-scope URLs normalize to task-only visibility.
Rationale: The source brief explicitly says system workflows remain unavailable from mobile task-card views.
Alternatives considered: Add mobile diagnostics access. Rejected as out of scope and contrary to ordinary task-card visibility.
Test implications: Existing UI tests remain final validation evidence.
