# Implementation Plan: Shareable Filter URL Compatibility

**Branch**: `302-shareable-filter-url` | **Date**: 2026-05-05 | **Spec**: `specs/302-shareable-filter-url/spec.md`
**Input**: Single-story feature specification from `/specs/302-shareable-filter-url/spec.md`

## Summary

Implement MM-589 by tightening the existing Tasks List URL parser, URL writer, and execution-list API validation so legacy shared links remain task-scoped, canonical filters support repeated and comma-encoded values, contradictory include/exclude filters fail clearly, empty lists normalize away, and cursor state resets when filters or page size changes. The main implementation surfaces are `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, `api_service/api/routers/executions.py`, and `tests/unit/api/routers/test_executions.py`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `frontend/src/entrypoints/tasks-list.tsx` uses `replaceUrlQuery` and `syncUrl` | add targeted URL normalization coverage | frontend unit |
| FR-002 | partial | legacy `state` and `repo` parse exists; URL currently writes `repoExact` | add tests for legacy task-safe links and adjust canonical exact repo behavior if needed | frontend unit |
| FR-003 | implemented_unverified | canonical params are emitted by `appendFilterParams` | add coverage for raw runtime values and canonical URL/API state | frontend unit |
| FR-004 | partial | comma parsing exists via `splitParam`; repeated values are not parsed | add repeated-value parsing in frontend and backend | frontend unit + API unit |
| FR-005 | missing | frontend chooses one mode; API currently accepts include and exclude together | add clear frontend/API validation errors | frontend unit + API unit |
| FR-006 | implemented_unverified | unsupported workflow scope detection and backend task scope filtering exist | add explicit tests for system/all/manifest fail-safe behavior | frontend unit + API unit |
| FR-007 | partial | filter changes reset cursors; page-size behavior needs explicit verification | add page-size cursor reset coverage and fix if failing | frontend unit |
| FR-008 | implemented_unverified | runtime chips use `formatRuntimeLabel` while URL uses raw values | preserve and extend tests | frontend unit |
| FR-009 | implemented_verified | MM-589 preserved in `spec.md` input | preserve in final verification and commit/PR metadata | final verify |
| DESIGN-REQ-006 | partial | URL sync and pagination reset exist | strengthen tests for page-size reset | frontend unit |
| DESIGN-REQ-016 | implemented_unverified | URL holds page size/cursor/sort/filter state | targeted URL-state tests | frontend unit |
| DESIGN-REQ-017 | implemented_unverified | UI and API task-scope guards exist | fail-safe tests | frontend unit + API unit |
| DESIGN-REQ-018 | partial | canonical include/exclude exists but repeated values and contradiction errors need work | implement parser/validation updates | frontend unit + API unit |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, Temporal Python SDK, React, TanStack Query, Zod, Vitest, Testing Library  
**Storage**: Existing Temporal visibility/search attribute state and execution projections only; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh`; focused frontend tests with `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`; focused API tests through `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`  
**Integration Testing**: Existing hermetic integration runner `./tools/test_integration.sh` when backend/Temporal integration behavior changes; this slice targets unit-level frontend/API boundaries  
**Target Platform**: Mission Control web UI and FastAPI execution-list endpoint  
**Project Type**: Web application with Python API and React frontend  
**Performance Goals**: Query parsing remains linear in number of filter params and does not add extra network round trips  
**Constraints**: Preserve task-only visibility, fail fast on ambiguous filters, no raw credentials, no new persistent tables, no compatibility aliases beyond explicitly requested legacy URL inputs  
**Scale/Scope**: One Tasks List URL/API story for MM-589

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I Orchestrate, Don't Recreate: PASS - changes stay inside existing UI/API orchestration surfaces.
- II One-Click Agent Deployment: PASS - no new services or required secrets.
- III Avoid Vendor Lock-In: PASS - no provider-specific integration introduced.
- IV Own Your Data: PASS - URL/API state remains local/operator-controlled.
- V Skills First-Class: PASS - no skill runtime contract changes.
- VI Scientific Method: PASS - tests are planned before implementation and verification is final authority.
- VII Runtime Configurability: PASS - no hardcoded deployment configuration added.
- VIII Modular Architecture: PASS - use existing Tasks List parser and execution router boundaries.
- IX Resilient by Default: PASS - fail-fast validation avoids ambiguous query behavior.
- X Continuous Improvement: PASS - final outcome will include structured evidence.
- XI Spec-Driven Development: PASS - spec, plan, tasks, and verification drive the change.
- XII Canonical Docs Separation: PASS - implementation notes remain under `specs/302-shareable-filter-url`; canonical docs are source requirements only.
- XIII Delete, Don't Deprecate: PASS - no internal compatibility shims; only explicit legacy URL inputs are supported as product behavior.

## Project Structure

### Documentation (this feature)

```text
specs/302-shareable-filter-url/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── tasks-list-url-state.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
frontend/src/entrypoints/
├── tasks-list.tsx
└── tasks-list.test.tsx

api_service/api/routers/
└── executions.py

tests/unit/api/routers/
└── test_executions.py
```

**Structure Decision**: Use the existing Mission Control Tasks List entrypoint and FastAPI execution-list router. The feature does not require new modules, database models, migrations, or routes.

## Complexity Tracking

No constitution violations.
