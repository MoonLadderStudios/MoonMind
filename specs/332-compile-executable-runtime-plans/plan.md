# Implementation Plan: Compile Executable Steps into Runtime Plans

**Branch**: `332-compile-executable-runtime-plans` | **Date**: 2026-05-09 | **Spec**: [spec.md](spec.md)  
**Input**: Single-story runtime specification from the MM-573 Jira preset brief.  
**Jira**: MM-573; source issue `manual-mm-569-mm-574`

## Summary

MM-573 requires execution to run from reviewed, flattened executable Tool and Skill steps rather than unresolved Preset placeholders or live catalog re-expansion. The current repository already has substantial contract and boundary behavior: canonical task step validation rejects unresolved `preset`/include steps, runtime planning turns explicit Tool/Skill steps into plan nodes, child Jira Orchestrate runs expand stored task-template provenance before execution, recursive preset metadata is preserved at task-shaped submission boundaries, and proposal promotion rejects unresolved preset steps while preserving preset provenance.

The remaining planning focus is to preserve MM-573 traceability and add focused verification coverage where the current evidence is indirect: Skill-step runtime materialization, promotion's no-live-reexpansion rule, and end-to-end evidence that durable executable payloads remain independent of live preset catalog changes after review/submit. Implementation should start test-first and only change code if those verification tests expose a gap.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `TaskStepSpec` rejects non-Tool/Skill step types; task-shaped submission tests assert no `preset` step remains after compiled preset submission. | Add/confirm boundary tests for durable executable payloads containing only Tool/Skill steps after submit and promotion. | unit + integration |
| FR-002 | implemented_verified | `_build_runtime_planner()` maps explicit Tool steps into typed skill/tool invocation plan nodes; `test_runtime_planner_preserves_authored_task_plan_tool_nodes` asserts tool identity, inputs, type, and source. | No code expected; keep traceability in tasks and final verify. | none beyond final verify |
| FR-003 | implemented_unverified | Runtime planner supports explicit multi-step payloads and `MoonMind.Run` dispatches `agent_runtime` child workflows or skill activities, but MM-573-specific Skill-step mapping proof should be explicit. | Add focused runtime planner test for explicit Skill step mapping to managed-session/activity materialization without changing Step Type semantics. | unit |
| FR-004 | implemented_verified | `TaskStepSource`, `AuthoredPresetBinding`, API/router tests, integration tests, and worker-runtime expansion preserve preset source/authored metadata without requiring live catalog for already-expanded steps. | No code expected unless final verify finds a missing field. | none beyond final verify |
| FR-005 | implemented_unverified | Proposal service tests preserve preset provenance and reject unresolved preset steps; provider approval test creates execution from stored snapshot. | Add/confirm promotion test that reviewed flattened payload is promoted as-is and does not re-expand from live preset catalog. | unit |
| FR-006 | implemented_verified | `TaskExecutionSpec` rejects `type: preset` and unresolved include work; plan parser rejects unresolved preset include nodes; proposal service rejects unresolved preset promotion. | No code expected; preserve regression coverage. | none beyond final verify |
| FR-007 | missing | `spec.md` preserves MM-573 and `manual-mm-569-mm-574`; this plan begins downstream preservation. | Preserve issue keys and original brief through plan, tasks, implementation notes, verification, commit text, and PR metadata. | final verify |
| SCN-001 | implemented_unverified | Runtime planner handles explicit executable steps. | Verify Tool and Skill plan node compilation together in an MM-573-focused test. | unit |
| SCN-002 | implemented_verified | Tool-step runtime planner test covers typed tool plan node mapping. | No new implementation expected. | none beyond final verify |
| SCN-003 | implemented_unverified | Runtime supports agent child workflow and skill activity dispatch, but explicit Skill-step mapping should be tested at planner boundary. | Add focused Skill-step runtime planner test. | unit |
| SCN-004 | implemented_verified | Preset provenance and authored preset metadata are preserved by task contract, API, integration, and proposal tests. | No new implementation expected. | none beyond final verify |
| SCN-005 | implemented_unverified | Proposal promotion tests preserve provenance and reject unresolved preset steps. | Add no-live-reexpansion assertion using a stored reviewed payload. | unit |
| SCN-006 | implemented_verified | Contract, plan parser, and proposal service reject unresolved Preset/include work. | No new implementation expected. | none beyond final verify |
| SC-001 | implemented_unverified | Tool-step compilation and unresolved-preset rejection are covered separately. | Add combined runtime compilation test for Tool/Skill and unresolved Preset behavior. | unit |
| SC-002 | implemented_verified | Existing recursive preset/API/integration tests preserve preset-derived source/authored metadata and do not require live catalog after expanded payload submission. | No new implementation expected. | none beyond final verify |
| SC-003 | implemented_unverified | Proposal tests cover preservation/rejection but not an explicit no-live-reexpansion negative control. | Add promotion regression that changing/removing catalog data cannot alter reviewed flattened payload. | unit |
| SC-004 | implemented_verified | Contract and proposal validation reject unresolved preset payloads with explicit errors. | No new implementation expected. | none beyond final verify |
| SC-005 | missing | Traceability is present in `spec.md` and this plan only. | Preserve traceability in all remaining MoonSpec artifacts and delivery metadata. | final verify |
| DESIGN-REQ-006 | implemented_unverified | Task contract rejects non-executable step types; submission tests assert expanded payload contains no `preset` steps. | Add/confirm MM-573-focused executable payload test. | unit + integration |
| DESIGN-REQ-007 | implemented_verified | Runtime planner test covers Tool step to typed plan node mapping. | No new implementation expected. | none beyond final verify |
| DESIGN-REQ-012 | implemented_unverified | Runtime planner and `MoonMind.Run` support skill/agent materialization paths. | Add explicit Skill-step mapping test. | unit |
| DESIGN-REQ-018 | implemented_verified | Contract, plan parser, and proposal tests reject unresolved Preset/include work. | No new implementation expected. | none beyond final verify |
| DESIGN-REQ-020 | implemented_verified | Task source/authored preset models and API/integration/proposal tests preserve provenance while executing flattened steps. | No new implementation expected. | none beyond final verify |
| DESIGN-REQ-021 | implemented_unverified | Proposal promotion validates stored payload and preserves provenance. | Add no-live-reexpansion promotion test. | unit |
| DESIGN-REQ-022 | implemented_unverified | Existing proposal tests reject unresolved preset steps but do not explicitly simulate live catalog drift. | Add explicit no-live-reexpansion regression. | unit |

## Technical Context

**Language/Version**: Python 3.12 for task contract, runtime planner, Temporal workflow, proposal service, API route, and tests; TypeScript/React only if Create-page submit behavior regresses.  
**Primary Dependencies**: Pydantic v2 task contract models, FastAPI execution route/service boundary, Temporal Python SDK runtime planner and workflow dispatch, existing task-template catalog, task proposal service, pytest.  
**Storage**: Existing Temporal execution records, artifact-backed original task input snapshots, task payload metadata, and proposal stored payloads only; no new persistent tables planned.  
**Unit Testing**: `./tools/test_unit.sh`; focused iteration with `pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/task_proposals/test_service.py -q`.  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage when API/execution boundary behavior changes; focused local check with `pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -m integration_ci -q` when needed.  
**Target Platform**: MoonMind API service and Temporal managed task execution on Linux containers.  
**Project Type**: Python backend workflow/control plane with existing React authoring surfaces.  
**Performance Goals**: Runtime planning remains linear in step count and bounded by existing step limits; no live preset catalog lookup after executable payload review/submit.  
**Constraints**: Preserve Temporal payload safety, avoid embedding large preset content in workflow history, do not mutate checked-in skill folders, do not add internal compatibility aliases, fail explicitly for unsupported runtime step types, preserve MM-573 and `manual-mm-569-mm-574` traceability.  
**Scale/Scope**: One runtime-contract slice spanning canonical task step validation, runtime plan generation, proposal promotion, and execution-boundary verification.

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan strengthens MoonMind orchestration contracts and uses existing agent/runtime adapters.
- II. One-Click Agent Deployment: PASS. No new services, secrets, or deployment prerequisites.
- III. Avoid Vendor Lock-In: PASS. Tool/Skill/Preset semantics are provider-neutral and adapter-backed.
- IV. Own Your Data: PASS. Durable executable payloads, provenance, and snapshots remain MoonMind-owned data.
- V. Skills Are First-Class: PASS. Skill steps remain first-class executable runtime units.
- VI. Replaceable Scaffolding / Thick Contracts: PASS. Work centers on explicit task and runtime plan contracts.
- VII. Runtime Configurability: PASS. Runtime/profile selection remains payload/config driven; no hardcoded provider behavior.
- VIII. Modular and Extensible Architecture: PASS. Work stays inside task contract, runtime planner, proposal, and boundary tests.
- IX. Resilient by Default: PASS. Unresolved Preset work fails before execution and reviewed executable payloads avoid catalog drift.
- X. Continuous Improvement: PASS. Planning preserves evidence and final verification requirements.
- XI. Spec-Driven Development: PASS. `spec.md`, this plan, and design artifacts preserve MM-573 before implementation.
- XII. Canonical Docs vs Tmp: PASS. Source docs remain desired-state; rollout and implementation notes live under `specs/332-compile-executable-runtime-plans/`.
- XIII. Pre-release Compatibility: PASS. No compatibility aliases or hidden transforms are planned; unsupported step types fail explicitly.

**Post-design re-check**: PASS. Phase 1 artifacts keep the same boundaries and introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/332-compile-executable-runtime-plans/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── runtime-step-plan-contract.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
└── workflows/
    ├── tasks/task_contract.py
    ├── temporal/worker_runtime.py
    ├── temporal/workflows/run.py
    └── task_proposals/service.py

api_service/
└── api/routers/executions.py

tests/
├── unit/workflows/tasks/test_task_contract.py
├── unit/workflows/temporal/test_temporal_worker_runtime.py
├── unit/workflows/task_proposals/test_service.py
├── unit/api/routers/test_executions.py
└── integration/temporal/test_task_shaped_submission_normalization.py
```

## Complexity Tracking

No constitution violations or extra complexity accepted.
