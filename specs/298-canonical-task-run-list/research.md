# Research: Canonical Task Run List Route

## FR-001 and FR-002 Canonical Routes

Decision: `implemented_verified`; preserve existing route behavior.
Evidence: `api_service/api/routers/task_dashboard.py` defines `/tasks/list`, `/tasks`, and `/tasks/tasks-list`; `tests/unit/api/routers/test_task_dashboard.py` covers root and alias redirects.
Rationale: The core canonical route and legacy redirect behavior already satisfy the spec.
Alternatives considered: Reworking route ownership was rejected because current route ownership is direct and covered.
Test implications: Final regression only unless related code changes affect route behavior.

## FR-003 Task-List Boot Identity

Decision: `implemented_unverified`; add verification tests first.
Evidence: `task_dashboard.py` renders page key `tasks-list`, `build_runtime_config("/tasks/list")`, and `data_wide_panel=True`; existing tests only assert dashboard config text for list and detail pages.
Rationale: The implementation appears present, but the test evidence does not prove the exact page key, wide layout payload, and initial path required by THOR-370.
Alternatives considered: Treating route render smoke tests as sufficient was rejected because the spec requires exact boot payload behavior.
Test implications: Unit test in `tests/unit/api/routers/test_task_dashboard.py`.

## FR-004 MoonMind-Owned Browser Data Access

Decision: `implemented_unverified`; add frontend verification.
Evidence: `frontend/src/entrypoints/tasks-list.tsx` fetches `${payload.apiBase}/executions`; boot payload uses `/api` paths.
Rationale: Browser behavior appears to use MoonMind API paths, but no focused test protects against direct provider URLs.
Alternatives considered: Relying on code inspection only was rejected because this is a security/privacy source requirement.
Test implications: Vitest assertion in `frontend/src/entrypoints/tasks-list.test.tsx`.

## FR-005 and FR-006 Ordinary Task Visibility

Decision: `partial`; code and tests are required.
Evidence: `api_service/api/routers/executions.py` maps `scope=tasks` to `WorkflowType="MoonMind.Run" AND mm_entry="run"`. `frontend/src/entrypoints/tasks-list.tsx` defaults to tasks but exposes `Scope`, `Workflow Type`, and `Entry` controls that can request `scope=all`, `scope=system`, and `entry=manifest`.
Rationale: The backend has a useful task-scope primitive, but the ordinary UI still permits broad workflow browsing from `/tasks/list`.
Alternatives considered: Keeping raw scope controls as advanced filters was rejected because the source design explicitly moves broad browsing to diagnostics.
Test implications: Vitest for ordinary query normalization; API-router tests for task-scope query construction; integration-style unit test with mixed task/system rows.

## FR-007 and DESIGN-REQ-015 Compatibility URLs

Decision: `missing`; implement fail-safe URL handling.
Evidence: Current tests expect `scope=all` to be exposed and `entry=manifest` to remain in the ordinary list URL. Current code initializes scope to `all` when `workflowType` or `entry` exists without explicit scope.
Rationale: Existing behavior conflicts with THOR-370's fail-safe compatibility requirement.
Alternatives considered: Passing broad params through to the API was rejected because manual URL edits must not expose system rows in the ordinary page.
Test implications: Cover at least `scope=system`, `scope=all`, system `workflowType`, and `entry=manifest`.

## FR-008 Diagnostics Separation

Decision: `missing`; define and implement permission-gated handoff or recoverable message.
Evidence: No normal-list code path was found that redirects authorized administrators to diagnostics or explains broad workflow movement for ordinary users.
Rationale: The story allows either diagnostics routing or recoverable messaging, but requires broad workflow browsing to be separate from the ordinary list.
Alternatives considered: Adding a new full diagnostics product was deferred; a bounded redirect/message contract is enough for this story if an explicit diagnostics surface is not available.
Test implications: Unit/UI coverage for ordinary and admin-compatible behavior; integration coverage only if a backend route is added.

## FR-009 Table and Control Surface

Decision: `partial`; remove ordinary broad workflow affordances and add tests.
Evidence: Desktop table columns omit forbidden columns, but the control deck exposes Scope, Workflow Type, and Entry; mobile card metadata includes `workflowType`.
Rationale: Column-only compliance is insufficient because the ordinary page still surfaces broad workflow browsing concepts.
Alternatives considered: Hiding only the table columns was rejected because the spec forbids ordinary broad workflow browsing, not just columns.
Test implications: Vitest queries should assert the normal page lacks broad workflow controls/columns and still provides task-relevant filters.

## FR-010 Authorization and Privacy

Decision: `partial`; ensure URL/filter handling cannot widen visibility.
Evidence: `list_executions` applies owner restrictions for non-admin users, but ordinary UI can still send broad workflow scope filters; source design also requires hidden values and params not to bypass authorization.
Rationale: Backend owner restrictions are necessary but not sufficient for ordinary task-list fail-safe behavior.
Alternatives considered: Relying only on owner filters was rejected because system workflows owned by the same user or visible to admins could still leak into ordinary list semantics.
Test implications: Unit/API tests for broad params under ordinary list behavior and UI tests for normalized requests.

## Test Tooling

Decision: Use `./tools/test_unit.sh` for final unit verification, targeted pytest/Vitest during implementation, and focused hermetic integration coverage in `tests/integration/api/test_tasks_list_visibility.py` for the mixed-row and broad-URL contracts.
Evidence: Repo instructions require `./tools/test_unit.sh`; existing Tasks List coverage is in pytest router tests and Vitest entrypoint tests.
Rationale: Most planned behavior is route, URL normalization, and UI state logic, but the story's core promise is an end-to-end boundary: ordinary `/tasks/list` must not expose system or manifest rows when mixed execution data exists. A focused hermetic integration test provides that proof without requiring live provider credentials.
Alternatives considered: Playwright-only validation was rejected because the behavior can be covered faster and more deterministically at unit and hermetic API/UI boundaries.
Test implications: Unit, UI, and focused hermetic integration tests are mandatory; compose-backed full integration should run through `./tools/test_integration.sh` when the new integration test is marked `integration_ci`.
