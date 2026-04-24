# Implementation Plan: Report-Aware Execution Projections

**Branch**: `248-report-aware-execution-projections` | **Date**: 2026-04-24 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/248-report-aware-execution-projections/spec.md`

## Summary

Plan MM-496 as a real runtime implementation story with a bounded first slice: add report-aware summary projection data to `/api/executions/{workflowId}` and explicitly defer the dedicated report endpoint. The repository already contains the compact report projection helper in `moonmind/workflows/temporal/report_artifacts.py`, but the execution detail API model and materialization path do not yet surface those fields. The implementation plan is therefore to reuse the existing projection helper, extend execution detail schemas and router materialization, add focused router/contract tests, and preserve explicit MM-496 traceability while keeping report refs artifact-backed and authorization-safe.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `build_report_projection_summary` in `moonmind/workflows/temporal/report_artifacts.py` already defines the bounded projection shape, but `ExecutionModel` in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` do not yet surface it. | Extend execution detail schema and materialization to expose the bounded projection. | unit + contract |
| FR-002 | partial | Helper logic already derives latest refs from the compact report bundle, but no execution detail path currently invokes it. | Thread server-side latest report projection into execution detail materialization. | unit + contract |
| FR-003 | implemented_partial | The helper already limits projection output to compact refs and bounded counts only. | Reuse the helper directly in execution detail materialization; add boundary tests proving no second storage/read model is introduced. | unit + contract |
| FR-004 | partial | Existing artifact APIs already enforce authorization and preview/default-read behavior, but execution detail does not yet surface report refs. | Ensure execution detail carries only artifact refs and does not bypass artifact-read controls; verify no raw artifact payloads are exposed. | unit + contract |
| FR-005 | planned | `docs/Artifacts/ReportArtifacts.md` treats the endpoint as optional future work, but no MM-496 feature-local artifact records the decision yet. | Preserve the explicit defer-now decision in plan, tasks, and later verification artifacts. | traceability review |
| FR-006 | partial | `build_report_projection_summary` supports `has_report = false`, but execution detail does not yet carry the projection. | Add safe no-report behavior tests at the execution detail boundary. | unit + contract |
| FR-007 | implemented_partial | The helper already rejects unsupported projection metadata keys and unsafe values. | Verify execution detail only passes bounded metadata through the helper and does not widen the allowed shape. | unit |
| FR-008 | partial | MM-496 is preserved in the Jira brief and spec only. | Preserve MM-496 throughout plan, tasks, and verification artifacts. | traceability review |
| DESIGN-REQ-013 | partial | Execution detail projection helper exists, but API wiring is missing. | Reuse helper and wire it into execution detail materialization. | unit + contract |
| DESIGN-REQ-022 | planned | Source doc recommends execution detail conveniences first and treats the endpoint as optional future work. | Implement the summary-field slice and explicitly defer the endpoint. | traceability review |
| DESIGN-REQ-024 | implemented_partial | Helper keeps projection compact and artifact-backed; execution detail does not yet expose it. | Preserve compact refs only and verify no second storage model or auth bypass is introduced. | unit + contract |

## Technical Context

**Language/Version**: Python 3.12 backend/runtime; existing TypeScript/React consumers remain downstream but are not the primary implementation surface for this slice 
**Primary Dependencies**: FastAPI, Pydantic v2, SQLAlchemy async ORM, existing Temporal artifact service/helpers 
**Storage**: Existing temporal artifact metadata tables and configured artifact store; no new persistent storage 
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_report_workflow_rollout.py` 
**Integration Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py` for the execution-detail API boundary; `./tools/test_integration.sh` only if implementation crosses into compose-backed persistence or serialization behavior beyond the existing contract test surface 
**Target Platform**: MoonMind API service execution detail route backed by Temporal execution records and artifact linkage 
**Project Type**: Backend runtime/API story with contract verification 
**Performance Goals**: Reuse bounded report projection helpers and keep execution detail reads compact; avoid unbounded artifact hydration or extra storage indirection 
**Constraints**: No second report storage model, no raw artifact payloads in execution detail, preserve artifact authorization behavior, keep endpoint defer decision explicit, preserve MM-496 traceability 
**Scale/Scope**: One story covering report-aware execution detail exposure for canonical reports

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS - reuses existing report artifact helpers and execution-detail boundaries instead of inventing a parallel report service.
- II. One-Click Agent Deployment: PASS - no new service, secret, or operator setup is introduced.
- III. Avoid Vendor Lock-In: PASS - the projection is an artifact/read-model contract, not provider-specific behavior.
- IV. Own Your Data: PASS - reports remain in MoonMind-managed artifact storage and execution detail carries refs only.
- V. Skills Are First-Class and Easy to Add: PASS - no skill runtime changes are required.
- VI. Replaceable AI Scaffolding: PASS - the work centers on durable API/runtime contracts and bounded helper reuse.
- VII. Powerful Runtime Configurability: PASS - existing execution/artifact configuration remains unchanged.
- VIII. Modular and Extensible Architecture: PASS - changes stay within execution schema/materialization and existing report helper boundaries.
- IX. Resilient by Default: PASS - projection data remains bounded, deterministic, and artifact-backed.
- X. Facilitate Continuous Improvement: PASS - later verification will capture the explicit endpoint defer decision and any remaining drift.
- XI. Spec-Driven Development: PASS - MM-496 is preserved through spec, plan, and later tasks/verification.
- XII. Canonical Documentation Separation: PASS - Jira/orchestration input remains under `local-only handoffs`, while this feature directory captures the selected runtime slice.
- XIII. Pre-release Compatibility Policy: PASS - no compatibility aliasing or hidden semantic transforms are introduced.

## Project Structure

### Documentation (this feature)

```text
specs/248-report-aware-execution-projections/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── execution-report-projection-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/temporal_models.py
└── workflows/temporal/report_artifacts.py

api_service/api/routers/
└── executions.py

tests/unit/api/routers/
└── test_executions.py

tests/unit/workflows/temporal/
└── test_report_workflow_rollout.py

tests/contract/
└── test_temporal_execution_api.py
```

**Structure Decision**: Keep MM-496 scoped to the existing report projection helper, execution detail schema/materialization, and execution API contract tests. The dedicated `/report` endpoint is explicitly deferred; this story's first runtime slice is bounded execution-detail projection exposure.

## Complexity Tracking

No constitution violations.
