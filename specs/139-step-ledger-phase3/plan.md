# Implementation Plan: Step Ledger Phase 3

**Branch**: `139-step-ledger-phase3` | **Date**: 2026-04-08 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/139-step-ledger-phase3/spec.md`

## Summary

Expose the workflow-owned latest-run progress and step-ledger contracts through `/api/executions` without inflating the execution detail payload. The API router will query `MoonMind.Run` for bounded progress and the full latest-run step ledger, the compatibility serializer will add `stepsHref`, and the OpenAPI/client artifacts will be regenerated so frontend consumers can adopt the new contract.

## Technical Context

**Language/Version**: Python 3.11, TypeScript for generated client artifacts  
**Primary Dependencies**: FastAPI, Temporal Python SDK client handles and workflow queries, Pydantic v2, openapi-typescript generation  
**Storage**: Existing execution projection rows for lifecycle metadata; workflow query state for latest-run progress and step ledger  
**Testing**: `pytest` targeted router/contract/workflow tests plus `./tools/test_unit.sh`  
**Target Platform**: Linux server workers and API service containers  
**Project Type**: Backend API contract plus generated frontend client surface  
**Performance Goals**: `GET /api/executions/{workflowId}` stays cheap for normal polling; `/steps` reads use workflow queries and return bounded latest-run rows only  
**Constraints**: Preserve latest-run semantics across Continue-As-New; do not inline full step rows into execution detail; do not fabricate step-ledger state for unsupported workflow types; no compatibility alias wrappers beyond the documented compatibility payload fields  
**Scale/Scope**: API route, serialization, compatibility detail wiring, OpenAPI regeneration, and tests only

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The API surfaces workflow-owned state directly instead of inventing a second step-tracking model in the control plane.
- **II. One-Click Agent Deployment**: PASS. The work stays within the existing API/OpenAPI/tooling path and does not add new operator prerequisites.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The router exposes the frozen schema models from earlier phases instead of inventing UI-specific JSON.
- **IX. Resilient by Default**: PASS. Latest-run semantics stay anchored on `workflowId`; query failures degrade safely instead of fabricating state.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This phase has a dedicated feature package and traceable tasks.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Canonical behavior stays in the existing docs; this plan captures only rollout work.
- **XIII. Pre-Release Delete, Don't Deprecate**: PASS. The change fills the canonical detail surface directly and does not introduce transitional alias payloads for the step ledger.

## Project Structure

### Documentation (this feature)

```text
specs/139-step-ledger-phase3/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── execution-steps.openapi-fragment.yaml
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/api/routers/executions.py                      # MODIFY: add progress hydration + steps route
api_service/api/routers/task_dashboard_view_model.py       # MODIFY: expose temporal steps endpoint in runtime config
moonmind/schemas/temporal_models.py                        # MODIFY: add progress/stepsHref to execution contract
moonmind/workflows/temporal/client.py                      # MODIFY: add workflow-query helper(s) for latest-run progress/ledger
tests/unit/api/routers/test_executions.py                  # MODIFY: route + serialization coverage
tests/contract/test_temporal_execution_api.py              # MODIFY: contract coverage for progress + /steps
tests/unit/api/routers/test_task_dashboard_view_model.py   # MODIFY: runtime config steps endpoint coverage
frontend/src/generated/openapi.ts                          # REGENERATE: progress + /steps route contract
```

**Structure Decision**: Keep workflow-owned step/progress state inside the existing `MoonMind.Run` queries. The API/router layer reads those queries through a small Temporal client helper and reuses the existing Pydantic schema models for response validation.

## Complexity Tracking

The main risk is read-path inconsistency between the projection-backed detail record and the workflow query-backed latest-run payloads during Continue-As-New rollover. Mitigation: anchor all reads on `workflowId`, validate query payloads through the canonical schema models, and add tests proving the returned `runId` follows the latest query result instead of stale projection assumptions.
