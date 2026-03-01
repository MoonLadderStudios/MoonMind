# Requirements Traceability: Task Cursor Pagination

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Implementation Owner | Validation Owner | Validation Strategy |
|---|---|---|---|---|---|---|
| DOC-REQ-001 | Task objective: server-side cursor keyset pagination | FR-001, FR-006, FR-007 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py`, `api_service/api/routers/agent_queue.py` | Queue backend | Queue backend tests | Repository/service tests verify keyset seek predicate, `limit+1` handling, and cursor generation. |
| DOC-REQ-002 | Task objective: default `limit=50` and bounded limits | FR-002, FR-016 | `api_service/api/routers/agent_queue.py`, `api_service/api/routers/task_dashboard.py`, `moonmind/workflows/agent_queue/service.py` | Queue API + dashboard API | Router/service tests | Router/service tests verify default 50 and clamp to 200 for oversized requests. |
| DOC-REQ-003 | Task objective: canonical ordering `created_at DESC, id DESC` | FR-004, FR-006 | `moonmind/workflows/agent_queue/repositories.py` | Queue backend | Repository tests | Repository tests assert ordering stability including same-timestamp tie breaks by id. |
| DOC-REQ-004 | Task objective: `GET /api/tasks` contract (`limit`, `cursor`, response fields) | FR-001, FR-008, FR-009, FR-010 | `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/agent_queue.py`, `moonmind/schemas/agent_queue_models.py` | Queue API + schemas | Router tests | Router tests verify response envelope includes `items`, `page_size`, `next_cursor` and compatibility metadata. |
| DOC-REQ-005 | Task objective: pagination applies after filters | FR-003 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py` | Queue backend | Repository/service tests | Repository/service tests verify filtered datasets page correctly with cursor boundaries. |
| DOC-REQ-006 | Task objective: opaque base64url cursor encoding | FR-005 | `moonmind/workflows/agent_queue/service.py` | Queue backend | Service tests | Service tests verify cursor encode/decode round-trip and invalid token rejection. |
| DOC-REQ-007 | Task objective: descending seek + `limit+1` semantics | FR-006, FR-007 | `moonmind/workflows/agent_queue/repositories.py`, `moonmind/workflows/agent_queue/service.py` | Queue backend | Repository/service tests | Service/repository tests verify next-page traversal without duplicate IDs across adjacent pages. |
| DOC-REQ-008 | Task objective: index support for ordering | FR-011 | `api_service/migrations/versions/202602210001_agent_queue_list_indexes.py`, `moonmind/workflows/agent_queue/models.py` | Data model + migrations | Model/schema tests | Migration/schema verification confirms `(created_at,id)` index support for task list ordering. |
| DOC-REQ-009 | Task objective: dashboard URL state and pagination controls | FR-012, FR-014, FR-015 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard_view_model.py` | Dashboard frontend | Dashboard tests | Dashboard tests verify page size selector, URL query sync, and next-button behavior using `next_cursor`. |
| DOC-REQ-010 | Task objective: filter changes reset cursor/page | FR-013 | `api_service/static/task_dashboard/dashboard.js` | Dashboard frontend | Dashboard tests | Dashboard tests verify filter/page-size changes clear cursor state and restart from first page. |
| DOC-REQ-011 | Task objective: rollout + validation coverage requirements | FR-017, FR-018 | `moonmind/`, `api_service/`, `tests/unit/workflows/agent_queue/test_service_pagination.py`, `tests/unit/api/routers/test_task_dashboard.py`, `tests/task_dashboard/test_queue_layouts.js` | Feature implementation set | Validation suite | Acceptance requires runtime code changes plus passing `./tools/test_unit.sh` and runtime scope checks. |

## Coverage Gate

- Row count matches source requirements: `11` `DOC-REQ-*` rows.
- Every `DOC-REQ-*` maps to FR coverage, implementation surfaces, and validation strategy.
- Runtime-vs-docs alignment is explicit through `DOC-REQ-011` and must be enforced during implementation.
