# Implementation Plan: Step Ledger Phase 6

**Branch**: `143-step-ledger-phase6` | **Date**: 2026-04-09 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/143-step-ledger-phase6/spec.md`

## Summary

Harden latest-run semantics for the step-ledger rollout. The backend will reconcile execution-detail run identity to workflow query truth during projection lag, Mission Control will keep secondary artifact reads aligned to the latest step-ledger run, and the completed step-ledger rollout trackers will be retired from `docs/Temporal/StepLedgerAndProgressModel.md`.

## Technical Context

**Language/Version**: Python 3.11, TypeScript + React 18  
**Primary Dependencies**: Temporal Python SDK query contract, FastAPI execution router, TanStack Query task-detail page  
**Storage**: Temporal workflow query state, existing projection tables, artifact APIs only  
**Testing**: `pytest tests/unit/api/routers/test_executions.py -q`, `pytest tests/contract/test_temporal_execution_api.py -q`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/api/routers/test_executions.py tests/contract/test_temporal_execution_api.py`  
**Target Platform**: `/api/executions` read path and Mission Control task-detail UI  
**Project Type**: Backend read-path hardening, frontend read alignment, doc tracker cleanup  
**Performance Goals**: Keep detail polling bounded; avoid fetching the full step ledger just to build `progress`; keep latest-run artifact reads correct without introducing cross-run mixing  
**Constraints**: Preserve public `ExecutionProgress` shape, keep `workflowId` as durable identity, avoid hidden compatibility wrappers, and keep degradation truthful when Temporal queries fail  
**Scale/Scope**: `MoonMind.Run` progress query contract, execution router detail hydration, task-detail artifact query keying, and tmp tracker retirement

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The workflow query remains the owner of latest-run truth; the router and UI only consume it.
- **IV. Own Your Data**: PASS. Artifact reads stay artifact-backed and run-scoped; no logs or step history move into workflow state.
- **VI. Thin Scaffolding, Thick Contracts**: PASS. The external `progress` contract stays stable while internal query reconciliation is tightened.
- **IX. Resilient by Default**: PASS. The work targets projection lag, degraded reads, and Continue-As-New correctness with automated coverage.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Phase 6 gets its own feature package and task list.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. The implementation removes finished tmp rollout bullets instead of leaving stale backlog text.
- **XIII. Pre-Release Delete, Don't Deprecate**: PASS. The cleanup retires completed rollout notes rather than preserving stale migration checklists.

## Project Structure

### Documentation (this feature)

```text
specs/143-step-ledger-phase6/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── latest-run-hardening-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/workflows/run.py            # MODIFY: expose latest queried run id on bounded progress payload
api_service/api/routers/executions.py                  # MODIFY: reconcile execution-detail run identity from progress-query truth
tests/unit/api/routers/test_executions.py              # MODIFY: latest-run reconciliation coverage
tests/contract/test_temporal_execution_api.py          # MODIFY: public-contract latest-run reconciliation coverage
frontend/src/entrypoints/task-detail.tsx               # MODIFY: keep execution-wide artifact reads aligned to the latest step-ledger run
frontend/src/entrypoints/task-detail.test.tsx          # MODIFY: browser coverage for latest-run artifact alignment
docs/Temporal/StepLedgerAndProgressModel.md*.md                           # MODIFY: retire completed step-ledger rollout tracker bullets
```

**Structure Decision**: Keep run reconciliation at the workflow-query and router boundary instead of building another projection or compatibility layer. The frontend only consumes the resulting latest-run identity.

## Complexity Tracking

The main risk is accidentally changing the public `progress` contract while trying to carry enough latest-run metadata to reconcile stale projections. Mitigation: keep the extra latest-run signal internal to the workflow/router exchange, validate the public `ExecutionProgressModel` unchanged, and add contract tests that assert `progress` stays bounded while `runId` is corrected.
