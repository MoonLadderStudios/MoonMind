# Implementation Plan: Compile-Time Preset Composition With Provenance Preservation

**Branch**: `341-compile-time-preset-composition` | **Date**: 2026-05-12 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime spec from the `MM-642` Jira preset brief.

## Summary

MM-642 requires task preset composition to be finalized in the control plane before execution submission: recursive preset includes are validated, flattened with manual steps into deterministic executable order, and preserved with compact authored-preset and per-step provenance. Current repository evidence from the related recursive preset compilation implementation indicates the production behavior and focused test coverage already exist; this orchestration therefore treats implementation as verification-first and only plans fallback edits if current tests expose drift.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `api_service/services/task_templates/catalog.py` emits recursive composition metadata; `tests/unit/api/test_task_step_templates_service.py` covers expansion | no new implementation; rerun focused tests | unit |
| FR-002 | implemented_verified | catalog validation tests cover missing/unavailable targets, cycles, conflicting aliases, mappings, and scope failures | no new implementation; rerun focused tests | unit |
| FR-003 | implemented_verified | catalog, API route, frontend, and integration tests assert flattened deterministic step order | no new implementation; rerun focused tests | unit + integration_ci |
| FR-004 | implemented_verified | `moonmind/workflows/tasks/task_contract.py`, route tests, and frontend tests preserve `authoredPresets` and `steps[].source` | no new implementation; rerun focused tests | unit + integration_ci |
| FR-005 | implemented_verified | task contract and worker runtime tests prevent unresolved preset include work from reaching worker-facing payloads | no new implementation; rerun focused tests | unit + integration_ci |
| FR-006 | implemented_verified | task-shaped submission integration coverage preserves snapshot metadata after catalog-independent submission | no new implementation; rerun focused tests | integration_ci |
| FR-007 | implemented_verified | API route and integration regressions assert manual-only submissions do not fabricate preset metadata | no new implementation; rerun focused tests | unit + integration_ci |
| FR-008 | implemented_unverified | `spec.md` preserves `MM-642`; downstream artifacts need final traceability check | preserve `MM-642` in all generated artifacts and final verification evidence | final verify |
| SCN-001 | implemented_verified | recursive preset route and catalog tests exercise compile-before-finalization behavior | no new implementation | unit + integration_ci |
| SCN-002 | implemented_verified | catalog validation tests cover explicit include-tree failures | no new implementation | unit |
| SCN-003 | implemented_verified | deterministic order tests exist across catalog/API/integration coverage | no new implementation | unit + integration_ci |
| SCN-004 | implemented_verified | task contract, API, frontend, and integration assertions cover provenance preservation | no new implementation | unit + integration_ci |
| SCN-005 | implemented_verified | worker runtime and task-shaped submission tests cover resolved worker payloads | no new implementation | unit + integration_ci |
| SCN-006 | implemented_verified | integration and snapshot metadata assertions cover catalog-independent reconstruction semantics | no new implementation | integration_ci |
| DESIGN-REQ-010 | implemented_verified | source invariant is represented by catalog compilation, task contract validation, worker runtime behavior, and tests | no new implementation | unit + integration_ci |
| DESIGN-REQ-011 | implemented_verified | source invariant is represented by snapshot/provenance tests and contract helpers | no new implementation | unit + integration_ci |
| SC-001 | implemented_verified | focused integration and catalog validation cover submitted tasks with recursive preset includes being validated before execution finalization | no new implementation | integration_ci |
| SC-002 | implemented_verified | catalog/API/frontend/integration evidence covers deterministic final order for equivalent valid draft inputs | no new implementation | unit + integration_ci |
| SC-003 | implemented_verified | task contract, API, frontend, and integration tests preserve source provenance for preset-derived and detached steps | no new implementation | unit + integration_ci |
| SC-004 | implemented_verified | worker runtime, task contract, and focused integration evidence show compiled tasks execute without live catalog lookup | no new implementation | unit + integration_ci |
| SC-005 | implemented_verified | task-shaped submission integration and snapshot metadata evidence cover reconstruction after live catalog changes | no new implementation | integration_ci |
| SC-006 | implemented_verified | API route and integration manual-only regressions preserve zero-preset behavior | no new implementation | unit + integration_ci |
| SC-007 | implemented_verified | `spec.md`, generated design artifacts, `tasks.md`, alignment report, and verification report preserve `MM-642`, the canonical brief, DESIGN-REQ-010, and DESIGN-REQ-011 | no new implementation | final verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control Create page payload behavior.  
**Primary Dependencies**: FastAPI route/service layer, SQLAlchemy async task template catalog, Pydantic v2 task contract models, Temporal Python SDK worker/runtime boundaries, React Create page.  
**Storage**: Existing task template tables, Temporal execution records, artifact-backed original task input snapshots, and task payload metadata only; no new persistent storage.  
**Unit Testing**: `./tools/test_unit.sh`; focused pytest and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` for Create page behavior.  
**Integration Testing**: `./tools/test_integration.sh` for required hermetic integration; focused iteration with `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci`.  
**Target Platform**: MoonMind API service, Mission Control Create page, and Temporal managed task execution on Linux.  
**Project Type**: Python backend workflow/control plane with React frontend entrypoint.  
**Performance Goals**: Recursive compilation remains bounded by existing flattened step limits and requires no live preset catalog lookup after submission.  
**Constraints**: Preserve Temporal payload compatibility, keep large template content out of workflow history, fail explicitly for invalid include trees, and do not add compatibility aliases for internal contracts.  
**Scale/Scope**: One task preset composition slice spanning catalog expansion, create/API normalization, task snapshots, and worker-facing payloads.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan strengthens MoonMind control-plane orchestration and does not recreate agent cognition.
- II. One-Click Agent Deployment: PASS. No new service, dependency, or deployment prerequisite.
- III. Avoid Vendor Lock-In: PASS. Uses MoonMind task contracts and preset metadata, not provider-specific behavior.
- IV. Own Your Data: PASS. Submitted snapshots and provenance remain MoonMind-owned data.
- V. Skills Are First-Class: PASS. Presets remain composable authoring inputs that compile to runtime-neutral task steps.
- VI. Replaceable Scaffolding / Thick Contracts: PASS. Work centers on explicit task and preset compilation contracts.
- VII. Runtime Configurability: PASS. Existing template catalog/runtime settings remain the control points.
- VIII. Modular Architecture: PASS. Boundaries remain catalog, create/API normalization, snapshot, and worker runtime modules.
- IX. Resilient by Default: PASS. Invalid preset trees fail before execution, and submitted work is reconstructable without live catalog drift.
- X. Continuous Improvement: PASS. Planning, tests, and verification artifacts preserve evidence for MM-642.
- XI. Spec-Driven Development: PASS. Spec, plan, tasks, implementation evidence, and verification remain traceable to MM-642.
- XII. Canonical Docs vs Tmp: PASS. Runtime rollout notes live under `specs/341-compile-time-preset-composition/`; canonical docs are source references only.
- XIII. Pre-release Compatibility: PASS. The plan avoids new compatibility aliases and relies on a single canonical compiled task contract.

## Project Structure

### Documentation (this feature)

```text
specs/341-compile-time-preset-composition/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-preset-composition-contract.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/executions.py
└── services/task_templates/catalog.py

moonmind/
└── workflows/
    ├── tasks/task_contract.py
    └── temporal/worker_runtime.py

frontend/
└── src/entrypoints/task-create.tsx

tests/
├── unit/api/test_task_step_templates_service.py
├── unit/api/routers/test_executions.py
├── unit/workflows/tasks/test_task_contract.py
├── unit/workflows/temporal/test_temporal_worker_runtime.py
├── integration/temporal/test_task_shaped_submission_normalization.py
└── frontend/src/entrypoints/task-create.test.tsx
```

**Structure Decision**: Use the existing backend, workflow, frontend, and test locations that already own task preset compilation and submission behavior.

## Complexity Tracking

No constitution violations or extra complexity accepted.
