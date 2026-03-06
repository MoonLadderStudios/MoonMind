# Quickstart: Task Cursor Pagination

## 1. Confirm runtime-mode scope

- Read `specs/043-task-cursor-pagination/spec.md` and verify `Implementation Intent` is runtime implementation.
- Treat this feature as runtime mode end-to-end: production code and tests are required.
- Do not consider docs/spec changes alone as completion.

## 2. Review implementation surfaces

- Queue router and alias:
  - `api_service/api/routers/agent_queue.py`
  - `api_service/api/routers/task_dashboard.py`
- Queue pagination internals:
  - `moonmind/workflows/agent_queue/repositories.py`
  - `moonmind/workflows/agent_queue/service.py`
- API list response models:
  - `moonmind/schemas/agent_queue_models.py`
- Dashboard URL state and controls:
  - `api_service/static/task_dashboard/dashboard.js`
  - `api_service/api/routers/task_dashboard_view_model.py`
- Core tests:
  - `tests/unit/workflows/agent_queue/test_service_pagination.py`
  - `tests/unit/api/routers/test_agent_queue.py`
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/task_dashboard/test_queue_layouts.js`

## 3. Implement backend cursor pagination behavior

- Enforce default `limit=50` and clamp into `1..200`.
- Decode/validate cursor tokens as opaque base64url JSON `(created_at,id)` payloads.
- Apply filters first, then keyset seek predicate, then canonical ordering.
- Fetch `limit+1`, return first `limit`, derive `next_cursor` from returned tail when more data exists.
- Keep compatibility metadata fields while ensuring `items`, `page_size`, and `next_cursor` are always present.

## 4. Validate index support and rollout safety

- Confirm index support exists for canonical ordering (`created_at`, `id`) and common filtered list scans.
- Keep offset path only for compatibility fallback; reject simultaneous `cursor` and `offset`.
- Ensure old clients without pagination params remain bounded by server defaults.

## 5. Implement dashboard pagination behavior

- Persist `limit` and optional `cursor` in URL query params.
- Offer page-size selector values `25`, `50`, `100` (default 50).
- Enable forward paging only when `next_cursor` is present.
- Reset cursor and cursor stack when any filter or page-size value changes.

## 6. Run required validation command

```bash
./tools/test_unit.sh
```

Notes:
- In WSL, this script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not replace this acceptance command with direct `pytest` invocation.

## 7. Runtime scope guard checks (implementation stage)

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope checks pass with production code and test changes represented.

## 8. Planning completion gate

Before running `/speckit.tasks`, ensure these artifacts exist:

- `specs/043-task-cursor-pagination/plan.md`
- `specs/043-task-cursor-pagination/research.md`
- `specs/043-task-cursor-pagination/data-model.md`
- `specs/043-task-cursor-pagination/quickstart.md`
- `specs/043-task-cursor-pagination/contracts/task-cursor-pagination.openapi.yaml`
- `specs/043-task-cursor-pagination/contracts/requirements-traceability.md`

## Validation Evidence (2026-03-01)

- `./tools/test_unit.sh`: passed (`911 passed`, dashboard Node runtime tests executed).
- `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`: passed (`runtime tasks=15, validation tasks=12`).
- Manual dashboard smoke checks (first page, next page, filter reset, URL refresh): pending interactive verification.
