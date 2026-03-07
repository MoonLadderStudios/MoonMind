# Research: Task Cursor Pagination

## Decision 1: Use keyset pagination with canonical descending ordering

- **Decision**: Paginate task lists with `ORDER BY created_at DESC, id DESC` and cursor boundary `(created_at,id)`.
- **Rationale**: Keyset seeks stay O(limit) and preserve deterministic ordering when new rows arrive.
- **Alternatives considered**:
  - Offset pagination (`page`/`offset`): rejected for deep-scan slowdown and duplicate/missing row risk under concurrent inserts.
  - Sorting on `id` only: rejected because recency semantics are defined by `created_at`.

## Decision 2: Encode cursors as opaque base64url JSON payloads

- **Decision**: Cursor token payload contains `created_at` (UTC ISO-8601) and `id`, encoded as base64url without exposing internals to clients.
- **Rationale**: Opaque tokens keep API contract stable while preserving exact seek boundary values.
- **Alternatives considered**:
  - Plain query tuple (`created_at`, `id`): rejected because it leaks internal pagination mechanics and increases client coupling.
  - Signed JWT cursor: rejected as unnecessary complexity for internal pagination state.

## Decision 3: Use descending seek predicate with `limit + 1`

- **Decision**: Apply seek clause for rows older than cursor, fetch `limit + 1`, trim to `limit`, derive `next_cursor` from last returned row.
- **Rationale**: Standard keyset pattern provides bounded work and reliable next-page detection.
- **Alternatives considered**:
  - Exact `limit` fetch + count query: rejected due to extra query cost.
  - Return cursor for extra row instead of returned tail: rejected because client boundary should represent the last visible row.

## Decision 4: Apply filters before pagination boundary conditions

- **Decision**: Compose status/type/other active filters first, then apply cursor seek predicate and ordering.
- **Rationale**: Ensures page traversal remains within the same filtered dataset.
- **Alternatives considered**:
  - Paginate first, then filter in memory: rejected because pages become sparse/inconsistent and violate API semantics.

## Decision 5: Clamp `limit` server-side with default 50 and max 200

- **Decision**: Default to `limit=50`; clamp request limits into `1..200` for both `/api/tasks` and queue list surfaces.
- **Rationale**: Protects server resources and enforces bounded behavior even for old clients.
- **Alternatives considered**:
  - Trust client-provided limit: rejected due to unbounded query risk.
  - Lower max (`<=100`): rejected because existing list endpoints already support up to 200 and compatibility should be maintained.

## Decision 6: Keep response backward compatible while promoting cursor metadata

- **Decision**: Always return `items`, `page_size`, `next_cursor`; keep legacy list metadata fields (`offset`, `limit`, `hasMore`) for compatibility.
- **Rationale**: Supports incremental rollout without breaking existing clients.
- **Alternatives considered**:
  - Hard cutover to new envelope only: rejected due to migration risk.
  - Keep offset-only envelope: rejected because it does not satisfy keyset contract requirements.

## Decision 7: Preserve offset path only as compatibility fallback

- **Decision**: Use cursor path for default list navigation; allow offset path only when explicitly requested and cursor absent.
- **Rationale**: Enables low-risk rollout while steering active clients to keyset behavior.
- **Alternatives considered**:
  - Remove offset path immediately: rejected due to unknown downstream consumers.
  - Keep offset as primary path: rejected because objective requires cursor-first behavior.

## Decision 8: Persist dashboard pagination state in URL and reset on filter changes

- **Decision**: Store `limit` and optional `cursor` in query params; clear cursor/cursor stack whenever filter or page-size changes.
- **Rationale**: Refresh/share behavior stays predictable and avoids stale-cursor confusion across filter sets.
- **Alternatives considered**:
  - Keep state in memory only: rejected because refresh and sharable links lose pagination position.
  - Preserve cursor across filter changes: rejected because cursor boundary is not valid for a different filter set.

## Decision 9: Verify index support for canonical ordering

- **Decision**: Require an ordering-supporting index on `(created_at, id)` and retain filtered companion indexes where beneficial.
- **Rationale**: Keyset query performance depends on index-assisted ordering/seek operations.
- **Alternatives considered**:
  - No dedicated index: rejected due to potential table scans and unstable deep-page latency.
  - Over-index every filter permutation: rejected initially to avoid write amplification; start with ordering + common filtered index.

## Decision 10: Keep runtime-vs-docs mode aligned with runtime intent

- **Decision**: Treat this feature as runtime implementation only; planning outputs must drive code/test changes, not docs-only completion.
- **Rationale**: Spec explicitly requires runtime deliverables and validation tests.
- **Alternatives considered**:
  - Consider planning as final completion: rejected because acceptance criteria require production behavior and tests.
