# Implementation Plan: Prepare Target-Aware Inputs

**Branch**: `run-jira-orchestrate-for-mm-631-prepare-10a604d1` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:33deef30-3a30-4229-8cae-32c434b9aad5/repo/specs/325-prepare-target-aware-inputs/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed Jira branch name does not match the script's numeric feature-branch guard. Planning continued from `.specify/feature.json`, which points to `specs/325-prepare-target-aware-inputs`.

## Summary

MM-631 requires target-aware prepared input delivery for the canonical runtime workflow: objective-scoped attachments and current-step-scoped attachments must be materialized, represented by a manifest, converted into derived image context as secondary artifacts, and delivered across `MoonMind.Run` and `MoonMind.AgentRun` boundaries without leaking unrelated step inputs. Current repo evidence shows the task contract, API submission normalization, Codex worker materialization, vision target context generation, and Codex text-first instruction filtering already cover parts of the behavior. The main gap is the Temporal `MoonMind.Run`/child `AgentRun` contract: prepared context refs are not yet carried or filtered at that boundary. The implementation should add tests first at the unit and workflow boundary levels, then add the smallest runtime contract and activity/workflow behavior needed to prepare, retain, and pass target-aware context refs.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/tasks/task_contract.py`, `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/agents/codex_worker/test_worker.py` | Preserve existing behavior; ensure new Temporal paths do not inline binary or derived content into instructions | unit regression |
| FR-002 | implemented_verified | `TaskInputAttachmentRef` in `moonmind/workflows/tasks/task_contract.py`; API and integration normalization tests reference `inputAttachments` | Reuse structured refs as source input for new runtime prepare contract | unit + integration |
| FR-003 | implemented_verified | `moonmind/vision/service.py`; `tests/unit/moonmind/vision/test_service.py` target-context tests | Reuse generated image context as secondary prepared context and reference it from the runtime contract | unit + integration |
| FR-004 | partial | Codex worker prepare exists in `moonmind/agents/codex_worker/worker.py`; `MoonMind.Run` has no `inputAttachments` handling beyond generic `inputRefs` | Add/extend Temporal prepare activity/service contract and invoke it before affected step execution | unit + integration |
| FR-005 | partial | Codex worker preserves target distinction in local manifest; `moonmind/workflows/temporal/workflows/run.py` does not carry prepared target context | Add compact prepared-context refs to `MoonMind.Run` state/step inputs while preserving objective vs step target distinction | workflow boundary integration |
| FR-006 | partial | Codex worker downloads attachments; no Temporal prepare evidence | Ensure runtime prepare downloads or resolves every objective/step attachment needed before step execution, failing before affected step runs | unit + integration |
| FR-007 | partial | `.moonmind/attachments_manifest.json` shape exists in Codex worker tests | Define canonical prepared manifest payload for runtime workflow and expose manifest ref/path through prepared context | unit + integration |
| FR-008 | partial | Codex step instruction filtering omits non-current step context; no Temporal child request evidence | Filter prepared context per logical step before step execution and planner/agent dispatch | unit + workflow boundary integration |
| FR-009 | missing | `MoonMind.Run` builds `AgentExecutionRequest` without prepared attachment/context refs for the represented step | Add child `AgentRun` request metadata/input refs containing only objective plus represented-step prepared context | workflow boundary integration |
| FR-010 | partial | `AgentExecutionRequest.inputRefs` exists; step ledger exists, but no prepared context refs are recorded | Retain bounded manifest/context refs in workflow state and step evidence without embedding large bodies | unit + workflow boundary integration |
| FR-011 | partial | Codex worker text-first adapter consumes manifest/vision index; generic runtime adapter boundary lacks target-aware contract | Define adapter-visible prepared context contract and verify adapters cannot invent target rules | unit + integration |
| FR-012 | partial | Codex worker fails on materialization download errors; no Temporal prepare failure path | Add explicit runtime prepare failure result/error handling before affected step execution | unit + integration |
| FR-013 | implemented_unverified | `spec.md` preserves `MM-631` and original Jira brief; plan preserves it here | Preserve `MM-631` through plan, design artifacts, tasks, verification, commit, and PR metadata | final verification |
| SCN-001 | partial | Codex manifest tests cover objective and step targets | Add Temporal prepare manifest scenario covering objective and multiple step targets | integration |
| SCN-002 | partial | Codex materialization and vision tests exist | Verify raw refs and derived context refs remain separate in runtime prepare result | unit + integration |
| SCN-003 | partial | Codex instruction composition proves current-step filtering | Add workflow-level test that step execution sees only relevant objective/current-step context | integration |
| SCN-004 | missing | Child `AgentRun` request lacks target-aware prepared context | Add child workflow dispatch test proving represented-step filtering | integration |
| SCN-005 | partial | Codex adapter behavior exists | Add adapter contract tests for prepared refs and target-rule preservation | unit |
| SCN-006 | partial | Codex worker fails on download errors | Add runtime prepare failure test before step dispatch | integration |
| SC-001 | partial | Codex worker instruction tests cover current step only | Add end-to-end workflow boundary validation for one objective and two step attachments | integration |
| SC-002 | partial | Codex worker writes manifest; workflow prepare does not | Add prepare result assertion that every attachment-aware runtime execution has a manifest ref | integration |
| SC-003 | missing | No child-run prepared-context assertion | Add child-run test proving no unrelated step context | integration |
| SC-004 | partial | Codex worker error path exists | Add Temporal prepare failure path before affected step runs | integration |
| SC-005 | implemented_unverified | `spec.md` and this plan preserve traceability | Preserve in generated tasks and final verify artifacts | final verification |
| DESIGN-REQ-001 | implemented_verified | Task contract and vision service preserve text vs structured/derived inputs | Preserve behavior while adding runtime refs | unit regression |
| DESIGN-REQ-002 | partial | `MoonMind.Run` owns step ledger but not target-aware prepare refs | Extend runtime workflow prepare/context delivery state | workflow boundary integration |
| DESIGN-REQ-003 | partial | Codex worker performs target-aware materialization | Move/extend behavior to canonical runtime boundary | unit + integration |
| DESIGN-REQ-004 | partial | Codex worker filters context per step | Add workflow-level filtering | integration |
| DESIGN-REQ-005 | missing | Child `AgentRun` request lacks prepared context boundary | Add represented-step prepared context contract | integration |
| DESIGN-REQ-006 | partial | Codex adapter consumes generated context; generic contract missing | Define adapter-visible target-aware prepared context contract | unit + integration |
| DESIGN-REQ-007 | partial | `MoonMind.Run` and `AgentRun` exist, but prepared input refs are not in their boundary | Add parent-owned prepared context refs for child dispatch and recovery evidence | workflow boundary integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, pytest, existing MoonMind artifact and vision services  
**Storage**: Existing artifact store plus workspace-local prepared files/manifest refs; no new persistent database tables planned  
**Unit Testing**: `./tools/test_unit.sh` and focused `pytest` targets through that runner  
**Integration Testing**: `./tools/test_integration.sh` for hermetic `integration_ci` coverage; focused Temporal workflow boundary tests where possible  
**Target Platform**: Linux service and managed worker containers  
**Project Type**: Backend workflow/control-plane feature with runtime adapter boundary impact  
**Performance Goals**: Prepared context selection must remain bounded by attachment count and must avoid embedding large/binary content in workflow history  
**Constraints**: Preserve secret hygiene; keep large bodies behind refs; fail explicitly on invalid preparation; avoid compatibility aliases for internal pre-release contracts unless Temporal in-flight safety requires an explicit versioned cutover  
**Scale/Scope**: One runtime story for objective-scoped and step-scoped input delivery across `MoonMind.Run`, step execution, and `MoonMind.AgentRun`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The plan adds orchestration/context delivery around existing agents, not a new cognitive engine.
- II. One-Click Agent Deployment: PASS. No mandatory external service or deployment prerequisite is introduced.
- III. Avoid Vendor Lock-In: PASS. Prepared context is a runtime contract with adapter-specific realization, not Codex-only behavior.
- IV. Own Your Data: PASS. Prepared files and context remain in MoonMind-controlled artifacts/workspaces.
- V. Skills Are First-Class and Easy to Add: PASS. The feature preserves selected-skill/runtime boundaries and does not mutate skill sources.
- VI. Replaceable Scaffolding, Thick Contracts: PASS. The plan defines explicit contracts and tests around volatile runtime adapter behavior.
- VII. Runtime Configurability: PASS. Existing artifact/vision settings remain the configuration surface; no hardcoded provider behavior is planned.
- VIII. Modular and Extensible Architecture: PASS. Work is planned at task contract, prepare activity/service, workflow, and adapter boundaries.
- IX. Resilient by Default: PASS. Explicit prepare failures and workflow-boundary tests are required.
- X. Facilitate Continuous Improvement: PASS. Diagnostics and artifact refs remain operator-visible evidence.
- XI. Spec-Driven Development: PASS. `spec.md`, this `plan.md`, and design artifacts preserve the MM-631 story.
- XII. Documentation Separation: PASS. Implementation tracking stays under `specs/325-prepare-target-aware-inputs/`.
- XIII. Pre-Release Velocity: PASS. The plan avoids compatibility aliases and requires updating callers/tests in one cohesive change.

Post-design re-check: PASS. The Phase 1 artifacts keep contracts explicit, store large bodies behind refs, and preserve runtime adapter boundaries without introducing new constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/325-prepare-target-aware-inputs/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── target-aware-prepared-context.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── tasks/task_contract.py
│   ├── temporal/workflows/run.py
│   ├── temporal/workflows/agent_run.py
│   ├── temporal/activity_runtime.py
│   └── adapters/
│       ├── codex_session_adapter.py
│       └── managed_agent_adapter.py
├── vision/service.py
└── agents/codex_worker/worker.py

api_service/
└── api/routers/executions.py

tests/
├── unit/
│   ├── workflows/tasks/test_task_contract.py
│   ├── moonmind/vision/test_service.py
│   ├── agents/codex_worker/test_attachment_materialization.py
│   ├── agents/codex_worker/test_worker.py
│   └── workflows/temporal/workflows/
└── integration/
    ├── temporal/test_task_shaped_submission_normalization.py
    └── workflows/temporal/workflows/
```

**Structure Decision**: Use the existing backend workflow, task contract, vision service, runtime adapter, and Temporal workflow test layout. No frontend, database migration, or new persistent storage tree is planned unless implementation discovers that existing artifact refs cannot express the required prepared context.

## Complexity Tracking

No constitution violations require justification.
