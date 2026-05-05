# Implementation Plan: Executions List and Facet API Support for Column Filters

**Branch**: `303-executions-list-facets` | **Date**: 2026-05-05 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/specs/303-executions-list-facets/spec.md`

## Summary

Implement the `MM-590` task-list backend contract by extending the existing Temporal-backed executions list route with bounded canonical filter parsing, server-side sort parameters, additional text/value filters, and a new facet request surface. The existing Tasks List client already sends several canonical filter params and falls safe to task scope, but it currently derives popover values from the loaded page and has no authoritative facet/error/fallback state. Unit tests will cover filter validation and query construction; integration/contract tests will cover list/facet request behavior and Tasks List client fallback behavior.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/api/routers/executions.py` builds Temporal queries for state/repo/runtime/skill/date but ignores `sort`/`sortDir` and lacks ID/title/repo text filters | add bounded sort parsing and missing supported filter params | unit + contract |
| FR-002 | partial | include/exclude helpers exist for some fields; blank handling exists for scheduled/finished only | add text filters, repeated/comma value handling bounds, date validation, and blank validation | unit + contract |
| FR-003 | partial | `ExecutionListResponse` returns `count`, `countMode`, and `nextPageToken`; sorting is current-page custom ordering rather than request-controlled | preserve count metadata and add sort query support without breaking pagination tokens | unit + contract |
| FR-004 | missing | no `/api/executions/facets` route or facet response model found | add facet models and Temporal-backed facet route for status/runtime/skill/repository/integration with counts and blank count | unit + contract |
| FR-005 | partial | `frontend/src/entrypoints/tasks-list.tsx` uses current-page values but does not call facets or show authoritative/fallback notice | add facet fetch path and visible fallback/error notice inside filter controls | frontend unit |
| FR-006 | partial | task scope and owner enforcement are tested in `tests/unit/api/test_executions_temporal.py`; no facet boundary exists | reuse task-scope/owner query for facets and test system value exclusion | unit + contract |
| FR-007 | partial | contradictory include/exclude validation exists for four pairs only | add structured validation for unsupported facets/sorts, invalid blank modes, oversize lists/text, and invalid date ranges | unit + contract |
| FR-008 | implemented_unverified | `spec.md` preserves `MM-590` and source mappings | keep traceability in artifacts, tasks, verification, and commit/PR metadata | final verify |
| SC-001 | partial | existing list tests cover some filters, not multi-filter plus sort | add test for multi-filter sorted query | contract |
| SC-002 | implemented_unverified | existing list pagination tests cover tokens and count | add coverage that filtered/sorted requests keep count and token behavior | contract |
| SC-003 | missing | no facet route | add facet test with values beyond current page via mocked Temporal page/count calls | contract |
| SC-004 | missing | no facet route | add test that requested facet filter is excluded while other filters remain | unit + contract |
| SC-005 | partial | validation errors exist for unknown scope and contradictory pairs | expand validation tests | unit |
| SC-006 | partial | owner/task-scope tests exist for list | add facet authorization/scope test | unit + contract |
| SC-007 | implemented_unverified | spec artifacts preserve Jira traceability | final verification | verify |
| DESIGN-REQ-006 | partial | list route partially supports current request and count | complete list sort/filter validation | unit + contract |
| DESIGN-REQ-019 | missing | no facet request surface | add facet contract and UI fallback notice | unit + contract + frontend |
| DESIGN-REQ-020 | partial | canonical params exist for state/repo/runtime/skill but validation is incomplete | complete canonical raw-value validation and contradictory rejection | unit + contract |
| DESIGN-REQ-025 | partial | list owner/task scope exists; facet path missing | ensure facets use same scoping and structured errors | unit + contract |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Tasks List client behavior  
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async session fixtures, Temporal Python SDK client surface, React, TanStack Query, Zod, Vitest, Testing Library  
**Storage**: Existing Temporal visibility/search attributes and canonical execution projection rows; no new persistent storage  
**Unit Testing**: `./tools/test_unit.sh` with targeted pytest and Vitest during iteration  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic integration suite; contract tests under `tests/contract/` provide API-boundary evidence  
**Target Platform**: MoonMind FastAPI service and Mission Control Tasks List frontend  
**Project Type**: Web service plus React frontend  
**Performance Goals**: Bound list/facet request values and text lengths; avoid unbounded facet scans; preserve existing page-size limits  
**Constraints**: Browser calls only MoonMind APIs; normal Tasks List remains task-run scoped; no direct Temporal or provider calls from browser; no raw query syntax exposed to users  
**Scale/Scope**: One independently testable Tasks List list/facet API story for `MM-590`

## Constitution Check

| Principle | Gate | Status |
| --- | --- | --- |
| I Orchestrate, Don't Recreate | Extend MoonMind API boundaries without replacing Temporal or agent behavior | PASS |
| II One-Click Agent Deployment | No new mandatory external dependency or storage | PASS |
| III Avoid Vendor Lock-In | Temporal-specific query behavior stays behind the existing executions API boundary | PASS |
| IV Own Your Data | Uses existing operator-controlled Temporal/projection data | PASS |
| V Skills First-Class | No skill runtime changes | PASS |
| VI Scientific Method | TDD tasks and final verification required | PASS |
| VII Runtime Configurability | Reuses request parameters and existing config; no hardcoded deployment secrets | PASS |
| VIII Modular Architecture | Changes remain in executions router/schema and Tasks List client boundary | PASS |
| IX Resilient by Default | Structured validation and bounded inputs prevent raw backend failures | PASS |
| X Continuous Improvement | Run ends with structured MoonSpec verification outcome | PASS |
| XI Spec-Driven Development | This plan follows `spec.md` and creates tests before implementation | PASS |
| XII Canonical Docs Desired State | No canonical docs changes planned; rollout stays in `specs/303-*` | PASS |
| XIII Delete, Don't Deprecate | No compatibility shims beyond current explicit URL/filter compatibility | PASS |

## Project Structure

### Documentation (this feature)

```text
specs/303-executions-list-facets/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── executions-list-facets.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/executions.py
moonmind/schemas/temporal_models.py
frontend/src/entrypoints/tasks-list.tsx

tests/unit/api/test_executions_temporal.py
tests/contract/test_temporal_execution_api.py
frontend/src/entrypoints/tasks-list.test.tsx
```

**Structure Decision**: Use the existing FastAPI executions router and Temporal schema models for the backend API contract, and the existing Tasks List React entrypoint for client-facing fallback/error behavior. No new service package or persistence layer is needed.

## Complexity Tracking

No constitution violations require complexity exceptions.
