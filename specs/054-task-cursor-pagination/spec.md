# Feature Specification: Task Cursor Pagination

**Feature Branch**: `043-task-cursor-pagination`  
**Created**: 2026-03-01  
**Status**: Draft  
**Input**: User description: "Implement pagination for tasks with server-side cursor keyset pagination, default `limit=50`, canonical ordering `created_at DESC, id DESC`, backend/API/dashboard updates, rollout safety, and validation tests. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Requirements

| DOC-REQ ID | Source | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | Task objective: "server-side cursor (keyset) pagination" | The tasks list must use cursor/keyset pagination rather than offset-only pagination for primary navigation. |
| DOC-REQ-002 | Task objective: "default `limit=50`" | The API must default task page size to 50 and enforce bounded limits. |
| DOC-REQ-003 | Task objective: "Sorting rule ... `ORDER BY created_at DESC, id DESC`" | Canonical task ordering must be deterministic on `created_at DESC, id DESC`. |
| DOC-REQ-004 | Task objective: API contract for `GET /api/tasks` | The endpoint must accept `limit` and `cursor` and return `items`, `page_size`, and `next_cursor`. |
| DOC-REQ-005 | Task objective: "Pagination must apply after filters" | Existing filters must be honored before keyset pagination boundaries are applied. |
| DOC-REQ-006 | Task objective: cursor encoding details | Cursor tokens must be opaque and encode `(created_at, id)` with base64url JSON payload semantics. |
| DOC-REQ-007 | Task objective: descending seek query + `limit + 1` | Keyset seek behavior must return rows older than cursor and derive `next_cursor` from the returned page boundary. |
| DOC-REQ-008 | Task objective: indexing section | Data storage must include an index supporting canonical ordering for efficient keyset scans. |
| DOC-REQ-009 | Task objective: dashboard URL + controls | Dashboard pagination state must persist in URL (`limit`, optional `cursor`) and expose page-size/next navigation UX. |
| DOC-REQ-010 | Task objective: "When any filter changes: reset cursor" | Dashboard filter changes must reset cursor pagination to the first page. |
| DOC-REQ-011 | Task objective rollout/testing + acceptance criteria | Delivery must include runtime code changes plus unit-test validation for first page, second page traversal, filters + reset behavior, and limit clamping. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Load First Page Reliably (Priority: P1)

As a dashboard user, I want the tasks list to load quickly with a bounded default page size so I can view recent work without unbounded queries.

**Why this priority**: A safe first-page default is the minimum behavior needed to protect performance and keep the list usable.

**Independent Test**: Request `GET /api/tasks` with no pagination params and verify at most 50 items are returned with a stable descending order.

**Acceptance Scenarios**:

1. **Given** tasks exist, **When** the client requests `GET /api/tasks` without `limit` or `cursor`, **Then** the response contains at most 50 items ordered by `created_at DESC, id DESC`.
2. **Given** the client provides an out-of-range limit, **When** the request is processed, **Then** the server clamps the page size into the allowed range.

---

### User Story 2 - Navigate Forward Without Gaps or Duplicates (Priority: P2)

As a dashboard user, I want to request the next page using an opaque cursor so pagination remains stable even while new tasks are inserted.

**Why this priority**: Correct forward navigation is the core value of keyset pagination and prevents offset-based inconsistency.

**Independent Test**: Fetch page 1, use `next_cursor` for page 2, and verify no duplicate IDs between pages while maintaining ordering.

**Acceptance Scenarios**:

1. **Given** a first page response with `next_cursor`, **When** the client requests `GET /api/tasks?cursor=<next_cursor>&limit=50`, **Then** the response returns the next contiguous slice of older tasks.
2. **Given** no rows remain after the current page, **When** the page is requested, **Then** the response sets `next_cursor` to `null`.

---

### User Story 3 - Combine Filtering and URL-Persisted Pagination (Priority: P3)

As a dashboard user, I want pagination state to persist in the URL and reset correctly when filters change so shared links and refresh behavior stay predictable.

**Why this priority**: Filtered browsing must remain deterministic and understandable across refresh/share flows.

**Independent Test**: Apply filters and pagination from the dashboard, refresh the page, and verify state restoration; change any filter and verify cursor resets to first page.

**Acceptance Scenarios**:

1. **Given** active filters and a page cursor in the URL, **When** the dashboard loads, **Then** it requests tasks using those query parameters.
2. **Given** a user changes any filter, **When** the dashboard reloads task data, **Then** the request omits prior cursor state and starts from page 1.

### Edge Cases

- Cursor token is malformed or cannot be decoded; the API rejects it with a clear client error.
- Cursor token is well-formed but points past available data; the API returns an empty `items` list with `next_cursor: null`.
- New tasks are inserted between page requests; forward paging does not duplicate already returned tasks.
- Multiple tasks share identical `created_at` timestamps; `id` ordering guarantees deterministic pagination.
- Old clients call the endpoint without pagination params; response remains bounded by default clamping rules.
- Filter set changes between requests while reusing an old cursor; client resets cursor and receives first page for new filter state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The tasks list endpoint MUST support cursor-based pagination with query parameters `limit` and `cursor`. (Maps: DOC-REQ-001, DOC-REQ-004)
- **FR-002**: The default page size MUST be `50`, and requested `limit` values MUST be clamped to the supported range `1..200`. (Maps: DOC-REQ-002)
- **FR-003**: Pagination MUST be applied after all active task filters (including existing status/runtime/outcome/search filters). (Maps: DOC-REQ-005)
- **FR-004**: The canonical ordering for paginated task lists MUST be `created_at DESC, id DESC`. (Maps: DOC-REQ-003)
- **FR-005**: Cursor tokens MUST be opaque to clients and encode `created_at` and `id` values using base64url JSON payload semantics. (Maps: DOC-REQ-006)
- **FR-006**: When a cursor is provided, the task query MUST return rows older than the cursor pair using descending keyset seek behavior on `(created_at, id)`. (Maps: DOC-REQ-003, DOC-REQ-007)
- **FR-007**: The backend MUST fetch `limit + 1` rows to determine whether another page exists, return only the first `limit`, and derive `next_cursor` from the last returned row when another page exists. (Maps: DOC-REQ-007)
- **FR-008**: `GET /api/tasks` responses MUST expose `items`, `page_size`, and `next_cursor`, where `next_cursor = null` indicates the end of the result set. (Maps: DOC-REQ-004)
- **FR-009**: The API MUST NOT require `total_count` by default; if exposed, it MUST be opt-in via `include_total=true`. (Maps: DOC-REQ-004)
- **FR-010**: Existing consumers of the tasks list endpoint MUST remain supported through backward-compatible response behavior during rollout. (Maps: DOC-REQ-004)
- **FR-011**: The data store MUST include an index that supports the canonical ordering on `(created_at DESC, id DESC)`. (Maps: DOC-REQ-008)
- **FR-012**: The dashboard MUST persist pagination state in URL query parameters using `limit` and optional `cursor`. (Maps: DOC-REQ-009)
- **FR-013**: When any dashboard filter changes, the dashboard MUST clear cursor state and request the first page for the new filter set. (Maps: DOC-REQ-010)
- **FR-014**: The dashboard MUST provide a page-size selector with values `25`, `50`, and `100`, defaulting to `50`. (Maps: DOC-REQ-009)
- **FR-015**: The dashboard MUST provide forward pagination control when `next_cursor` is present and MUST suppress forward navigation control when `next_cursor` is `null`. (Maps: DOC-REQ-009)
- **FR-016**: The rollout MUST bound old-client requests by enforcing server-side pagination defaults even when clients do not send explicit pagination params. (Maps: DOC-REQ-002, DOC-REQ-004)
- **FR-017**: Required deliverables MUST include production runtime code changes across backend, data-access/query behavior, and dashboard integration; docs-only or spec-only changes are insufficient. (Maps: DOC-REQ-011)
- **FR-018**: Required deliverables MUST include validation tests covering first page behavior, second page cursor traversal, filters with pagination reset behavior, and limit clamping. (Maps: DOC-REQ-011)

### Key Entities *(include if feature involves data)*

- **TaskListPageRequest**: Client-visible request state combining filters with pagination inputs (`limit`, optional `cursor`).
- **TaskListCursor**: Opaque token representing the last row boundary using `created_at` and `id`.
- **TaskSummary**: Task list item payload returned in `items`.
- **TaskListPageResponse**: Paginated response envelope with `items`, `page_size`, and `next_cursor`.
- **TaskListOrderingIndex**: Persistent ordering support for efficient keyset reads over `created_at` and `id`.

### Assumptions & Dependencies

- Task identifiers are strictly comparable and stable for deterministic ordering ties.
- `created_at` is present for every listed task and can be used as the primary temporal sort key.
- Existing task filters already behave correctly and are reusable with keyset pagination.
- Dashboard routing/state management can persist and react to query-string pagination parameters.

### Non-Goals

- Offset/page-number pagination for deep navigation.
- Mandatory previous-page API support (`prev_cursor`) in the initial rollout.
- Always-on total-count computation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Default task list requests return no more than 50 items in 100% of validation test runs.
- **SC-002**: Sequential page retrieval using `next_cursor` yields zero duplicate task IDs across adjacent pages in validation scenarios.
- **SC-003**: Under normal insert load test conditions, page traversal preserves deterministic ordering with no missing rows relative to keyset boundaries.
- **SC-004**: Dashboard filter changes always reset pagination to the first page in validation tests.
- **SC-005**: Schema/index verification confirms an ordering index exists for `(created_at DESC, id DESC)`.
- **SC-006**: Required pagination validation tests pass via `./tools/test_unit.sh`.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Completed runtime implementation across repository/service/router/dashboard surfaces for cursor pagination, clamped defaults, and backward-compatible response metadata.
- Completed validation command `./tools/test_unit.sh` with passing unit and dashboard runtime tests.
- Completed `DOC-REQ-*` implementation/validation traceability with explicit ownership in `contracts/requirements-traceability.md`.

### MEDIUM/LOW remediation status

- Completed deterministic artifact updates across `spec.md`, `plan.md`, and `tasks.md`.
- Completed runtime scope gate command `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- Deferred manual interactive dashboard smoke checks to follow-up verification because this remediation run is non-interactive.
