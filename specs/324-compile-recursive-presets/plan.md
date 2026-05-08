# Implementation Plan: Compile Recursive Task Presets

**Branch**: `run-jira-orchestrate-for-mm-630-compile-428a2508` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story runtime spec from the MM-630 Jira preset brief.

## Summary

MM-630 requires task preset composition to be finalized before execution: recursive includes are validated, flattened into deterministic executable steps, and preserved with enough authored-preset and step-source provenance to audit or reconstruct submitted work after live preset catalog changes. The repo already has recursive catalog expansion with cycle/inactive/input validation and several task-contract provenance fields. The remaining planning gap is to make the submission/snapshot boundary authoritative for compiled recursive presets, including authored preset bindings and include-tree summaries, with unit coverage for catalog and normalization behavior plus a hermetic integration check proving workers receive resolved steps without live catalog dependency.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/services/task_templates/catalog.py` recursively expands includes before returning steps; Create auto-expands unresolved presets before submit | ensure create/API submitted task snapshot records compiled include-tree metadata as authoritative before execution | unit + integration |
| FR-002 | implemented_unverified | catalog tests cover cycles, inactive includes, incompatible input mappings, personal/global scope violations, and flattened step limits | add or extend focused tests for missing/unauthorized include targets if not already covered by current errors | unit |
| FR-003 | implemented_unverified | catalog expansion appends resolved steps in deterministic order and frontend submits expanded steps | add boundary test from recursive preset draft through submitted task payload order | unit + integration |
| FR-004 | partial | `TaskExecutionSpec` accepts `authoredPresets` and `steps[].source`; frontend preserves `source`; API preserves `authoredPresets` only when supplied | derive and persist reliable authored preset binding/include-tree summary from expansion output for recursive presets | unit + integration |
| FR-005 | implemented_unverified | worker runtime expands top-level task templates before child execution and docs say workers consume flattened steps; current tests cover executable expanded steps | add integration coverage that execution receives resolved steps and no unresolved preset step remains | integration_ci |
| FR-006 | partial | task snapshots and `appliedStepTemplates` exist, but applied template metadata currently omits recursive `composition`/include-tree summary | preserve compiled include-tree summary and provenance in task snapshot so reconstruction does not depend on live catalog | unit + integration |
| FR-007 | implemented_unverified | normalization preserves runtime, publish, Jira provenance, attachments, and template metadata in existing API/integration tests | keep existing behavior and add regression assertions around preset compilation path | unit + integration |
| FR-008 | missing | `spec.md` preserves MM-630 and the original Jira preset brief | preserve MM-630 through plan, tasks, implementation, verification, commit text, and PR metadata | final verify |
| SCN-001 | partial | catalog expansion validates include tree; submission boundary proof is incomplete | test invalid include tree blocks submission/execution finalization | unit + integration |
| SCN-002 | implemented_unverified | expansion appends resolved steps deterministically | test repeated equivalent submissions produce the same step order and IDs where expected | unit |
| SCN-003 | partial | frontend/source and task contract preserve step source; authored bindings are not consistently synthesized from expansion | add provenance derivation/preservation tests | unit + integration |
| SCN-004 | implemented_unverified | worker receives expanded steps from task template expansion | add catalog-unavailable-after-submit scenario at boundary | integration_ci |
| SCN-005 | partial | snapshots exist, but recursive composition summary is not preserved in applied template metadata | add snapshot reconstruction assertions after simulated catalog change | unit + integration |
| SCN-006 | implemented_unverified | manual-only task paths already have broad create/normalization coverage | add regression that manual-only tasks do not gain preset metadata | unit |
| SC-001 | partial | include validation exists at expansion time | prove validation occurs before execution finalization for task submissions | unit + integration |
| SC-002 | implemented_unverified | deterministic order appears present in catalog expansion | add deterministic-order regression | unit |
| SC-003 | partial | reliable step source is preserved; authored preset include-tree details need stronger persistence | implement and test provenance completeness | unit + integration |
| SC-004 | implemented_unverified | worker-facing paths use expanded steps | add live-catalog-unavailable verification | integration_ci |
| SC-005 | partial | task snapshots preserve task payloads but not full recursive composition summary | preserve and verify reconstruction metadata | unit + integration |
| SC-006 | implemented_unverified | manual-only submissions are established | retain and assert unchanged manual-only behavior | unit |
| DESIGN-REQ-001 | partial | task normalization preserves supplied metadata but does not synthesize missing compiled include-tree bindings | extend normalization/submission metadata preservation | unit + integration |
| DESIGN-REQ-002 | partial | recursive compile and validation exists in catalog; final execution contract lacks full composition metadata | persist compiled composition at task boundary | unit + integration |
| DESIGN-REQ-003 | implemented_unverified | task contract models support source and authored preset fields | add targeted validation around recursive authored bindings | unit |
| DESIGN-REQ-004 | partial | source/authored metadata are supported for reconstruction; recursive composition summary is incomplete | preserve include-tree summary for audit/reconstruction | unit + integration |
| DESIGN-REQ-005 | implemented_unverified | execution plane consumes resolved steps and tests cover expanded template execution shape | add boundary proof that workers do not require live catalog after submission | integration_ci |
| DESIGN-REQ-006 | partial | compile-time composition exists; provenance durability is incomplete | close provenance durability gap | unit + integration |
| DESIGN-REQ-007 | implemented_unverified | attachment and snapshot invariants are existing behavior; out of scope for new behavior | no implementation unless regression appears | final verify |
| DESIGN-REQ-008 | implemented_unverified | prepare/step attachment context is out of scope | no implementation unless preset changes disturb existing behavior | final verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Create page submission behavior if frontend provenance payloads change.  
**Primary Dependencies**: FastAPI route/service layer, SQLAlchemy async task template catalog, Pydantic v2 task contract models, Temporal Python SDK worker/runtime boundaries, React Create page.  
**Storage**: Existing task template tables, Temporal execution records, artifact-backed original task input snapshots, and existing task payload metadata only; no new persistent tables planned.  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration with pytest paths and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` when frontend payload behavior changes.  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage of task-shaped submission/execution boundary; no provider credentials required.  
**Target Platform**: MoonMind API service, Mission Control Create page, and Temporal managed task execution on Linux.  
**Project Type**: Python backend workflow/control plane with existing React frontend entrypoint.  
**Performance Goals**: Recursive compilation remains bounded by existing maximum flattened step count; no live preset catalog lookup is needed after submission.  
**Constraints**: Preserve Temporal payload compatibility; keep large content out of workflow history; do not mutate checked-in skill folders; do not add internal compatibility aliases; fail explicitly for invalid include trees.  
**Scale/Scope**: One task preset compilation slice spanning catalog expansion, create/API normalization, task snapshots, and worker-facing task payloads.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan strengthens MoonMind control-plane orchestration and does not recreate agent cognition.
- II. One-Click Agent Deployment: PASS. No new service, external dependency, or deployment prerequisite.
- III. Avoid Vendor Lock-In: PASS. Uses MoonMind task contracts and template metadata, not provider-specific behavior.
- IV. Own Your Data: PASS. Submitted snapshots and provenance remain MoonMind-owned data.
- V. Skills Are First-Class: PASS. Presets/skills stay composable authoring inputs that compile to runtime-neutral task steps.
- VI. Replaceable Scaffolding / Thick Contracts: PASS. Work centers on explicit task and preset compilation contracts.
- VII. Runtime Configurability: PASS. Existing template catalog/runtime settings remain the control points; no hardcoded deployment changes.
- VIII. Modular Architecture: PASS. Work stays inside catalog, create/API normalization, snapshot, and worker boundary modules.
- IX. Resilient by Default: PASS. Invalid or unresolved preset trees fail before execution, and submitted work is reconstructable without live catalog drift.
- X. Continuous Improvement: PASS. Planning, tests, and verification artifacts preserve evidence for MM-630.
- XI. Spec-Driven Development: PASS. `spec.md`, this plan, design artifacts, tasks, implementation, and verification remain traceable to MM-630.
- XII. Canonical Docs vs Tmp: PASS. Runtime rollout notes live under `specs/324-compile-recursive-presets/`; canonical docs are source references only.
- XIII. Pre-release Compatibility: PASS. The plan avoids new compatibility aliases and favors a single canonical compiled task contract.

## Project Structure

### Documentation (this feature)

```text
specs/324-compile-recursive-presets/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── task-preset-compilation-contract.md
└── checklists/
    └── requirements.md
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
├── integration/temporal/test_task_shaped_submission_normalization.py
└── frontend task-create Vitest coverage when Create payload behavior changes
```

## Complexity Tracking

No constitution violations or extra complexity accepted.
