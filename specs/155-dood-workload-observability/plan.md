# Implementation Plan: DooD Workload Observability

**Branch**: `155-dood-workload-observability` | **Date**: 2026-04-12 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/155-dood-workload-observability/spec.md`

## Summary

Implement Phase 4 of the Docker-out-of-Docker plan by making one-shot Docker-backed workload runs observable through durable artifacts, bounded execution metadata, and task/step detail surfaces. The runtime change enriches the existing Phase 1-3 workload request, launcher, and executable-tool bridge so stdout, stderr, diagnostics, declared outputs, and optional session association metadata are available without treating workload containers as managed sessions or publishing workload outputs as session continuity artifacts.

## Technical Context

**Language/Version**: Python 3.10+ runtime code, Pydantic v2 schemas, TypeScript/React Mission Control UI where detail presentation changes are needed  
**Primary Dependencies**: Existing workload models and registry, `DockerWorkloadLauncher`, executable tool bridge, Temporal run/step ledger, task-run detail API, artifact services, live-log/observability event surfaces  
**Storage**: Existing operator-controlled workflow/task artifact storage and per-run workload artifact directories; no new database table planned unless execution projection tests show the existing step metadata cannot carry workload links  
**Testing**: `./tools/test_unit.sh` with focused pytest coverage for workload artifact publication, declared output validation, tool result mapping, Temporal workflow boundary metadata, API projection, and focused frontend tests if UI rendering changes  
**Target Platform**: MoonMind Temporal deployment using the existing Docker-capable `agent_runtime` fleet and Mission Control task detail surfaces  
**Project Type**: Python service/runtime modules plus optional web dashboard presentation updates inside the existing MoonMind monorepo  
**Performance Goals**: Keep workflow/tool result payloads bounded while making full stdout/stderr/diagnostics retrievable from artifacts; artifact finalization should add negligible overhead compared with workload execution and container cleanup  
**Constraints**: Runtime mode; required deliverables include production runtime code changes and validation tests; no docs/spec-only completion; preserve executable-tool workload boundary; do not publish workload output as session continuity artifacts by default; keep large logs out of workflow history  
**Scale/Scope**: Phase 4 one-shot workload observability only; no Phase 5 policy hardening, Phase 6 Unreal image pilot, generic helper-container lifecycle, or new managed-agent runtime semantics

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Coverage |
| --- | --- | --- |
| I. Orchestrate, Don't Recreate | PASS | Extends MoonMind's control-plane workload/tool orchestration and does not create a competing agent model. |
| II. One-Click Agent Deployment | PASS | Reuses existing worker, artifact, and dashboard surfaces; no mandatory new external service or deployment prerequisite. |
| III. Avoid Vendor Lock-In | PASS | Uses portable artifact references and generic workload metadata, not vendor-specific result storage. |
| IV. Own Your Data | PASS | Workload logs, diagnostics, metadata, and declared outputs remain in operator-controlled artifact storage. |
| V. Skills Are First-Class | PASS | Docker-backed tools gain explicit output/artifact behavior consistent with executable tool contracts. |
| VI. Bittersweet Lesson | PASS | Keeps artifact publication and projection behind replaceable workload/tool contracts with tests as the anchor. |
| VII. Powerful Runtime Configurability | PASS | Uses request/profile-defined metadata and existing artifact roots without hardcoded external dependencies. |
| VIII. Modular and Extensible | PASS | Changes stay within workload schema/launcher/tool bridge and existing execution-detail projection boundaries. |
| IX. Resilient by Default | PASS | Artifacts and bounded metadata make failures diagnosable after container removal; workflow-boundary coverage is planned for compatibility-sensitive payloads. |
| X. Continuous Improvement | PASS | Structured diagnostics and artifact refs support run summaries and future improvement signals. |
| XI. Spec-Driven Development | PASS | Spec, plan, research, data model, contract, quickstart, and later tasks trace the runtime scope. |
| XII. Canonical Docs vs tmp | PASS | This plan is feature-scoped under `specs/`; no canonical docs are converted into migration checklists. |
| XIII. Delete, Don't Deprecate | PASS | No compatibility aliases or parallel legacy observability paths are introduced. |

**Post-Design Recheck**: PASS. The Phase 0/1 artifacts preserve the same runtime boundaries: one-shot workload observability, artifact-backed durable truth, bounded metadata in workflow/tool results, and session association as grouping context only.

## Project Structure

### Documentation (this feature)

```text
specs/155-dood-workload-observability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── contracts/
    └── workload-observability-contract.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── workload_models.py
├── workloads/
│   ├── docker_launcher.py
│   └── tool_bridge.py
└── workflows/
    └── temporal/
        └── workflows/
            └── run.py

api_service/
└── api/
    └── routers/
        └── task_runs.py

frontend/
└── src/
    └── components/
        └── task-detail/

tests/
└── unit/
    ├── workloads/
    ├── workflows/
    │   └── temporal/
    └── api/
        └── routers/
```

**Structure Decision**: Keep durable workload output publication in `moonmind/workloads/` because the launcher owns stdout/stderr/diagnostic finalization. Keep tool output mapping in `tool_bridge.py`, workflow/step linkage tests at the Temporal run boundary, and task/detail projection changes in the existing API/UI surfaces that already present run artifacts and observability events.

## Complexity Tracking

No constitution violations require complexity waivers.
