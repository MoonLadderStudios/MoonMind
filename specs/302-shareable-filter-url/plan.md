# Implementation Plan: Shareable Filter URL Compatibility

**Branch**: `302-shareable-filter-url` | **Date**: 2026-05-05 | **Spec**: `specs/302-shareable-filter-url/spec.md`
**Input**: Single-story feature specification from `/specs/302-shareable-filter-url/spec.md`

## Summary

Implement MM-589 by tightening the existing Tasks List URL parser, URL writer, and execution-list API validation so legacy shared links remain task-scoped, canonical filters support repeated and comma-encoded values, contradictory include/exclude filters fail clearly, empty lists normalize away, and cursor state resets when filters or page size changes. The main implementation surfaces are `frontend/src/entrypoints/tasks-list.tsx`, `frontend/src/entrypoints/tasks-list.test.tsx`, `api_service/api/routers/executions.py`, and `tests/unit/api/routers/test_executions.py`.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `frontend/src/entrypoints/tasks-list.tsx` URL sync; `frontend/src/entrypoints/tasks-list.test.tsx` page-size cursor reset | no new implementation | final verify |
| FR-002 | implemented_verified | legacy workflow-scope URL test in `frontend/src/entrypoints/tasks-list.test.tsx` | no new implementation | final verify |
| FR-003 | implemented_verified | `appendFilterParams` and repeated-runtime frontend test preserve canonical raw values | no new implementation | final verify |
| FR-004 | implemented_verified | `splitParamValues`, `raw_query_values`, repeated frontend/API tests | no new implementation | final verify |
| FR-005 | implemented_verified | `validateInitialFilterParams`, `validate_non_contradictory`, contradiction frontend/API tests | no new implementation | final verify |
| FR-006 | implemented_verified | unsupported workflow-scope UI behavior and API task-scope assertions | no new implementation | final verify |
| FR-007 | implemented_verified | page-size cursor reset test plus existing filter reset behavior | no new implementation | final verify |
| FR-008 | implemented_verified | runtime chip labeling test and existing `formatRuntimeLabel` behavior | no new implementation | final verify |
| FR-009 | implemented_verified | MM-589 preserved in `spec.md`, `tasks.md`, and `verification.md` | no new implementation | final verify |
| DESIGN-REQ-006 | implemented_verified | URL sync and cursor reset tests | no new implementation | final verify |
| DESIGN-REQ-016 | implemented_verified | canonical URL state tests | no new implementation | final verify |
| DESIGN-REQ-017 | implemented_verified | legacy fail-safe tests | no new implementation | final verify |
| DESIGN-REQ-018 | implemented_verified | repeated-value and contradiction validation tests | no new implementation | final verify |

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
