# Research: Desktop Columns and Compound Headers

## FR-001 / FR-002 / DESIGN-REQ-010

Decision: implemented_verified; preserve the current desktop column list and excluded columns.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` defines ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished. UI tests assert Kind, Workflow Type, Entry, and Started are absent.
Rationale: The desired default column set already matches the current implementation.
Alternatives considered: Rebuild column rendering from a new schema; rejected because the existing tuple model is sufficient.
Test implications: UI unit coverage remains required after the header refactor.

## FR-003 Through FR-006 / DESIGN-REQ-011

Decision: implemented_verified; use a reusable compound table header that renders a sort button and a separate filter button/popover.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` renders compound header controls. `frontend/src/entrypoints/tasks-list.test.tsx` verifies sort and filter targets remain independent.
Rationale: A small local component keeps the behavior consistent across visible columns without changing row rendering.
Alternatives considered: Keep top filters and add icon-only controls later; rejected because the story explicitly moves filtering into headers.
Test implications: UI tests must prove label clicks do not open filters and filter clicks do not change sort.

## FR-008 Through FR-010 / DESIGN-REQ-008

Decision: implemented_verified; status and repository controls moved from the top control grid into column popovers, runtime filtering was added, and active chips remain in the control deck.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` owns status, repository, and runtime filter state through header popovers. `frontend/src/entrypoints/tasks-list.test.tsx` covers active chip behavior and clear filters.
Rationale: This directly implements the first version of the column-filter model while preserving live updates, page size, and pagination outside the filter controls.
Alternatives considered: Implement every future filter type from the design, including date ranges; rejected as outside the MM-587 acceptance criteria and too broad for one story.
Test implications: UI tests for status/repository/runtime popovers, chips, clear filters, URL state, and API request parameters.

## SC-005 Runtime API Filter

Decision: implemented_verified; `GET /api/executions` accepts optional `targetRuntime` for Temporal task list queries and filters by `mm_target_runtime`.
Evidence: `api_service/api/routers/executions.py` includes `targetRuntime` in the Temporal visibility query. `tests/unit/api/routers/test_executions.py` verifies the task-scoped query includes `mm_target_runtime`.
Rationale: Runtime column filters should constrain fetched task results, not only current-page client rows.
Alternatives considered: Client-side runtime filtering only; rejected because it would be misleading across paginated Temporal results.
Test implications: API route-boundary test confirms `mm_target_runtime` is included with task scope.
