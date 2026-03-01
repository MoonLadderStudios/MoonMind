# Data Model: Task Cursor Pagination

## Entity: TaskListPageRequest

- **Description**: Server-side request shape for one task-list page fetch.
- **Fields**:
  - `status` (string | null)
  - `type` (string | null)
  - `summary` (boolean)
  - `limit` (integer)
  - `cursor` (string | null)
  - `offset` (integer, compatibility path only)
- **Rules**:
  - `limit` defaults to `50` and is clamped to `1..200`.
  - `cursor` and `offset` cannot be combined.
  - Empty cursor string is normalized to `null`.

## Entity: TaskListCursor

- **Description**: Opaque continuation token derived from the last row returned in a page.
- **Fields**:
  - `created_at` (UTC datetime)
  - `id` (UUID)
- **Encoding**:
  - JSON payload -> base64url string (padding optional).
- **Rules**:
  - Invalid payloads are rejected as client validation errors.
  - Cursor boundary represents the oldest visible row in the current page.

## Entity: TaskListQueryBoundary

- **Description**: Repository-level keyset seek condition used for descending pagination.
- **Predicate**:
  - `created_at < cursor_created_at`
  - OR `created_at = cursor_created_at AND id < cursor_id`
- **Rules**:
  - Applied only when cursor is present.
  - Applied after all active filters.

## Entity: TaskSummary

- **Description**: One row in the task list payload (`items[]`).
- **Key Fields**:
  - `id` (UUID)
  - `type` (string)
  - `status` (enum)
  - `payload` (object)
  - `priority` (integer)
  - `createdAt` (datetime)
  - `updatedAt` (datetime)
- **Rules**:
  - Ordering is always `created_at DESC, id DESC`.
  - Multiple rows with identical `created_at` are tie-broken by `id`.

## Entity: TaskListPageResponse

- **Description**: Paginated response envelope for `GET /api/tasks`.
- **Fields**:
  - `items` (TaskSummary[])
  - `page_size` (integer)
  - `next_cursor` (string | null)
  - `offset` (integer, compatibility)
  - `limit` (integer, compatibility)
  - `hasMore` (boolean, compatibility)
- **Rules**:
  - `next_cursor = null` indicates end-of-results.
  - `items.length <= page_size`.
  - `page_size` reflects effective clamped limit.

## Entity: TaskListOrderingIndex

- **Description**: Persistent DB index support for keyset ordering and seek operations.
- **Fields**:
  - `created_at`
  - `id`
- **Rules**:
  - Must support canonical descending order scans efficiently.
  - Companion filtered indexes (for example `status + created_at + id`) are optional but recommended for dominant filter patterns.

## Entity: DashboardPaginationState

- **Description**: Client-side state for `/tasks/list` page navigation.
- **Fields**:
  - `limit` (25 | 50 | 100)
  - `cursor` (string | null)
  - `cursorStack` (string[])
  - `nextCursor` (string | null)
  - `hasMore` (boolean)
  - `pageStart` (integer)
  - `pageEnd` (integer)
- **Rules**:
  - URL query always reflects active `limit` and optional `cursor`.
  - Any filter change resets `cursor`, `cursorStack`, and page range to first-page state.

## State Transitions

- **First page load**: `cursor = null` -> fetch newest `limit` rows.
- **Next page**: set `cursor = next_cursor` from prior response.
- **Filter change**: clear cursor state and request first page for new filter set.
- **End of results**: response with `next_cursor = null`; disable forward paging.

## Validation Constraints

- Malformed cursor token returns 422-style validation error.
- Cursor that points past data returns `items=[]` and `next_cursor=null`.
- Oversized limit requests are clamped; unbounded scans are disallowed.
