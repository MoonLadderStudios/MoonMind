# Implementation Plan: Task Cursor Pagination

**Branch**: `043-task-cursor-pagination` | **Date**: 2026-03-01 | **Spec**: `specs/043-task-cursor-pagination/spec.md`  
**Input**: Feature specification from `/specs/043-task-cursor-pagination/spec.md`

## Summary

Implement keyset cursor pagination for task list APIs and dashboard navigation using canonical ordering `created_at DESC, id DESC`, with server-side default `limit=50` (clamped to `1..200`), opaque cursor tokens, filter-aware seek queries, and index-backed performance. The selected orchestration mode is runtime (not docs-only), so completion requires production runtime code changes and validation tests.

## Technical Context

**Language/Version**: Python 3.11 backend services + vanilla JavaScript dashboard  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async repository/service stack, existing queue API schemas/view models  
**Storage**: PostgreSQL `agent_jobs` table and queue-list indexes (`created_at`, `id`, optional filtered variants)  
**Testing**: `./tools/test_unit.sh` (mandatory unit-test entrypoint)  
**Target Platform**: Linux/Docker Compose MoonMind API + worker + `/tasks/*` dashboard routes  
**Project Type**: Multi-service backend + static frontend dashboard  
**Performance Goals**: O(limit) page reads for deep pagination; avoid offset-scan slowdown; deterministic ordering under concurrent inserts  
**Constraints**: Preserve backward-compatible list response shape for existing consumers; clamp unbounded/oversized limits; pagination applies after active filters; filter changes reset dashboard cursor; keep runtime-vs-docs behavior aligned with runtime implementation intent  
**Scale/Scope**: Queue-backed task lists for `/api/queue/jobs` and `/api/tasks` alias, plus dashboard list pagination controls/state

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Pre-Design Gate

- **I. One-Click Deployment with Smart Defaults**: PASS. Changes stay within existing API/dashboard services and keep safe defaults (`limit=50`) without new secrets or bootstrap steps.
- **II. Powerful Runtime Configurability**: PASS. Pagination behavior remains request/config driven (`limit`, `cursor`, filters) with deterministic clamping.
- **III. Modular and Extensible Architecture**: PASS. Work remains within existing queue repository/service/router and dashboard state modules.
- **IV. Avoid Exclusive Proprietary Vendor Lock-In**: PASS. Cursor pagination and response contracts are datastore/API patterns not tied to a proprietary vendor.
- **V. Self-Healing by Default**: PASS. Deterministic keyset boundaries reduce race-induced duplicates/misses versus offset paging.
- **VI. Facilitate Continuous Improvement**: PASS. Explicit pagination metadata (`page_size`, `next_cursor`) improves observability and troubleshooting.
- **VII. Spec-Driven Development Is the Source of Truth**: PASS. Plan includes complete `DOC-REQ-*` mapping and runtime acceptance validation.
- **VIII. Skills Are First-Class and Easy to Add**: PASS. No skill execution contracts are changed; pagination surfaces are additive.

### Post-Design Re-Check

- PASS. Phase 1 outputs (`research.md`, `data-model.md`, `contracts/`, `quickstart.md`) preserve additive runtime behavior and existing module boundaries.
- PASS. No constitution violations require Complexity Tracking exceptions.

## Project Structure

### Documentation (this feature)

```text
specs/043-task-cursor-pagination/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── task-cursor-pagination.openapi.yaml
│   └── requirements-traceability.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/agent_queue_models.py
└── workflows/agent_queue/
    ├── repositories.py
    └── service.py

api_service/
├── api/routers/
│   ├── agent_queue.py
│   ├── task_dashboard.py
│   └── task_dashboard_view_model.py
├── migrations/versions/
│   └── *agent_queue*_indexes.py
└── static/task_dashboard/dashboard.js

tests/
├── unit/workflows/agent_queue/
│   ├── test_repositories.py
│   └── test_service_pagination.py
├── unit/api/routers/
│   ├── test_agent_queue.py
│   ├── test_task_dashboard.py
│   └── test_task_dashboard_view_model.py
└── task_dashboard/test_queue_layouts.js
```

**Structure Decision**: Reuse the existing queue list stack (`repository -> service -> router`) and dashboard state management without introducing new services or UI frameworks.

## Phase 0 - Research Summary

Research outcomes in `specs/043-task-cursor-pagination/research.md` establish:

1. Canonical sort and seek condition: `ORDER BY created_at DESC, id DESC` with cursor `(created_at,id)`.
2. Opaque base64url JSON cursor encoding/decoding with strict validation.
3. `limit + 1` fetch strategy to derive `next_cursor` and keep reads bounded.
4. Filter-first query composition before pagination boundaries.
5. Backward-compatible response strategy while promoting cursor metadata.
6. Dashboard URL/state behavior: persist `limit` and `cursor`; reset cursor on filter changes.
7. Runtime-mode alignment gate: docs-only completion is invalid for this feature.

## Phase 1 - Design Outputs

- **Data Model**: `data-model.md` defines request/query/cursor/response entities and dashboard pagination state transitions.
- **API Contract**: `contracts/task-cursor-pagination.openapi.yaml` defines `GET /api/tasks` query/response/error semantics, including cursor metadata and compatibility fields.
- **Traceability**: `contracts/requirements-traceability.md` maps each `DOC-REQ-*` to FRs, implementation surfaces, and validation strategy.
- **Execution Guide**: `quickstart.md` defines runtime-mode implementation flow and required validation command.

## Implementation Strategy

### 1. Backend Pagination Contract

- Keep `GET /api/tasks` and `GET /api/queue/jobs` list behavior aligned.
- Enforce default `limit=50` with server clamp `1..200`.
- Treat empty cursor as `null`, reject malformed cursor, and reject `cursor + offset` mixed requests.
- Return `items`, `page_size`, `next_cursor` always; preserve legacy fields (`offset`, `limit`, `hasMore`) for compatibility.

### 2. Repository/Service Keyset Query Path

- Use descending seek predicate for cursor path:
  - `created_at < cursor_created_at`
  - or `created_at = cursor_created_at AND id < cursor_id`
- Apply active filters before seek predicate.
- Fetch `limit + 1`, return first `limit`, and generate `next_cursor` from the last returned row when additional rows exist.
- Keep deterministic ordering for duplicate timestamps using `id DESC` tie-breaker.

### 3. Index and Performance Safeguards

- Ensure ordering index coverage on `(created_at DESC, id DESC)` (or equivalent B-tree usage for desc sort).
- Retain/verify filtered index support for common list predicates (for example status + created_at + id).
- Avoid deep offset scans on cursor path; keep offset path as compatibility fallback only.

### 4. Dashboard Pagination UX and URL State

- Persist `limit` and optional `cursor` in `/tasks/list` query params.
- Provide page-size selector `25 / 50 / 100` (default 50).
- Enable forward paging only when `next_cursor` is present.
- Maintain optional client-side cursor stack for Previous behavior.
- Reset cursor stack to first page whenever any list filter or page size changes.

### 5. Validation Strategy

- Add/maintain unit tests for:
  - first page default bounded to 50,
  - second-page traversal via `next_cursor` without duplicates,
  - filter + pagination interactions (including reset behavior),
  - limit clamping and invalid cursor handling,
  - index-backed query ordering behavior at repository level.
- Run `./tools/test_unit.sh` as acceptance gate.

### 6. Runtime-vs-Docs Mode Alignment Gate

- Feature intent is runtime implementation.
- Planning artifacts must lead to production code/test changes in `moonmind/`, `api_service/`, and `tests/`.
- Docs/spec-only completion does not satisfy this feature's acceptance criteria.

## Remediation Gates (Prompt B)

- Every `DOC-REQ-*` row must map to at least one FR, implementation surface, and validation strategy.
- Cursor semantics across repository, service, router, and dashboard must remain consistent on ordering and token handling.
- Pagination defaults/clamps must be server-enforced so old clients cannot request unbounded data.
- Tasks generated in `/agentkit.tasks` must include runtime code changes and test validation entries.

## Prompt B Remediation Application (Step 12/16)

### Completed CRITICAL/HIGH remediations

- Applied runtime cursor pagination across `repository -> service -> router -> /api/tasks alias`.
- Applied dashboard pagination URL state (`limit`, `cursor`), page-size controls (`25/50/100`), next/previous cursor flow, and filter-reset behavior.
- Added/updated unit validations for cursor encode/decode, first-page clamp behavior, filtered pagination boundaries, and router response metadata.
- Executed `./tools/test_unit.sh` with passing results.

### Completed MEDIUM/LOW remediations

- Updated `contracts/requirements-traceability.md` with explicit implementation and validation owners for all `DOC-REQ-001..011`.
- Recorded validation evidence in `quickstart.md` including runtime scope gate command output.
- Updated `tasks.md` execution status to reflect completed runtime and validation work.

### Remediations intentionally deferred

- Manual interactive dashboard smoke checks remain pending because they require a browser session and operator interaction not available in this run context.
- New index migration file `202603010001_task_cursor_pagination_indexes.py` was not added because index coverage already exists via `202602210001_agent_queue_list_indexes.py`; adding duplicate index DDL would create migration churn without behavior change.

## Risks & Mitigations

- **Cursor/order drift across layers**: define one canonical ordering in repository/service and assert in tests.
- **Compatibility regressions for existing clients**: preserve response shape and offset fallback while defaulting to cursor path.
- **Filter-reset UX regressions**: centralize pagination reset helper and cover with dashboard tests.
- **Index mismatch in deployed DBs**: verify index migration presence and include schema check in rollout validation.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _None_ | — | — |
