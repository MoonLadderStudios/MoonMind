# Implementation Plan: Resume from Last Failed Step

**Branch**: `change-jira-issue-mm-602-to-status-in-pr-5652b805` | **Date**: 2026-05-07 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:222b2e78-d472-440c-8bff-8e20c3cfd8f8/repo/specs/310-resume-from-last-failed-step/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` could not complete because the managed branch name is `change-jira-issue-mm-602-to-status-in-pr-5652b805`, while the script expects a numeric feature branch. Planning used `.specify/feature.json`, which points to `specs/310-resume-from-last-failed-step`.

## Summary

MM-602 adds a distinct failed-step Resume path for `MoonMind.Run`: a failed task may create a linked follow-up execution from the original task input snapshot, restore completed prior work from durable checkpoint evidence, and start new work at the last failed step. Repo gap analysis found related foundations but no failed-step Resume implementation: task input snapshots, rerun creation, step ledger query models, and task-detail intervention controls exist, while `canResumeFromFailedStep`, resume checkpoint contracts, a `resume-from-failed-step` command, preserved-step materialization, related-run labels, and boundary coverage are missing. The implementation should add tests first across schema/service/API/workflow/UI boundaries, then implement the smallest cohesive resume contract.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
|----|--------|----------|--------------|----------------|
| FR-001 | missing | `ExecutionActionCapabilityModel` has `canResume` but not `canResumeFromFailedStep`; `_build_action_capabilities()` only enables pause/resume lifecycle and rerun actions | Add failed-step Resume capability and disabled reasons based on source eligibility and checkpoint evidence | unit + API integration |
| FR-002 | missing | `/api/executions/{workflow_id}/rerun` and `RequestRerun` exist, but no failed-step resume command or linked follow-up relation exists | Add resume command/service path that creates a new execution and leaves source unchanged | unit + integration |
| FR-003 | partial | Rerun source metadata supports `sourceWorkflowId`/`sourceRunId`; no resume source pinning exists | Add `resumeSource` identity and require both source workflow/run IDs for checkpoint validation and provenance | unit + integration |
| FR-004 | partial | Original task input snapshot persistence exists; rerun may override/update inputs in existing update paths | Add resume request validation that rejects edited task payload values and uses the original snapshot unchanged | unit + contract |
| FR-005 | partial | Step ledger rows have refs/artifact slots; no resume checkpoint model or checkpoint artifact contract exists | Add compact resume checkpoint model/ref contract using existing artifact store for large evidence | unit + integration |
| FR-006 | missing | No resume eligibility or checkpoint validation service found | Add validation for source state, ownership, source identity, snapshot, plan, preserved refs, prepared refs, and workspace/branch state | unit + integration |
| FR-007 | missing | No failed-step Resume failure path exists | Add fail-fast resume errors before failed-step execution; no full-rerun fallback | unit + integration |
| FR-008 | missing | Step ledger query exists but no preserved-step status/provenance materialization exists | Extend resumed run initialization/progress to mark prior steps as preserved and start at failed step | workflow boundary + integration |
| FR-009 | partial | Task detail renders regular lifecycle `Resume` and rerun/edit actions; no failed-step Resume affordance or relationship label exists | Add distinct UI action, disabled reasons, confirmation/success copy, related-run display, and preserved-step rendering | frontend unit + API integration |
| FR-010 | partial | Step ledger endpoint exists and `mm_updated_at` patterns exist; no resume checkpoint/preserved-step diagnostics | Surface bounded checkpoint validation and preserved-step materialization evidence in task details/progress | unit + integration |
| FR-011 | missing | Existing tests cover rerun, snapshots, step ledger, and lifecycle Resume; none cover failed-step Resume | Add boundary tests for eligibility, checkpoint validation, preserved-step materialization, failed restoration, UI capability rendering | unit + integration_ci where feasible |
| FR-012 | implemented_verified | `spec.md` preserves MM-602 and the canonical Jira preset brief | Preserve traceability through plan, tasks, verification, commit, and PR metadata | final verify |
| SC-001 | missing | No `canResumeFromFailedStep` action exists | Add eligible failed-task detail behavior | frontend unit + API integration |
| SC-002 | missing | Resume creation path absent | Add linked follow-up creation with source unchanged and source run pinned | unit + integration |
| SC-003 | missing | No preserved-step representation | Add preserved-step materialization and rendering | workflow boundary + frontend unit |
| SC-004 | missing | No resume validation path | Add explicit validation errors before new work starts | unit + integration |
| SC-005 | missing | Resume request absent; edited payload rejection absent | Add strict request schema and validation | contract + unit |
| SC-006 | missing | Related-run UI exists only for remediation-adjacent metadata, not resume | Add source/resumed cross-links and label | API integration + frontend unit |
| SC-007 | missing | No failed-step Resume tests found | Add required coverage matrix | unit + integration |
| SC-008 | implemented_unverified | Traceability exists in spec; later artifacts not yet generated | Carry MM-602 through plan/tasks/verify/PR | final verify |
| DESIGN-REQ-001 | partial | Task architecture desired state; code lacks resume intent | Add explicit resume intent fields and validation | unit + integration |
| DESIGN-REQ-002 | missing | No implementation path for failed-step Resume | Add command and execution behavior | workflow boundary + integration |
| DESIGN-REQ-003 | partial | Step ledger refs/artifacts exist; no resume checkpoint/preserved provenance | Add checkpoint and preserved-step provenance | unit + integration |
| DESIGN-REQ-004 | partial | `MoonMind.Run` exposes step ledger query but cannot start at failed step from checkpoint | Add start-from-checkpoint execution branch | workflow boundary |
| DESIGN-REQ-005 | missing | Capability model lacks failed-step Resume | Add capability field and disabled reasons | unit + frontend unit |
| DESIGN-REQ-006 | missing | UI has lifecycle Resume only | Add failed-step Resume interaction flow | frontend unit + API integration |
| DESIGN-REQ-007 | partial | Existing failed task actions include edit/rerun; no failed-step Resume distinction | Preserve additive actions and keep terminal source immutable | frontend unit + API integration |
| DESIGN-REQ-008 | missing | No source-eligible resume command found | Add command preconditions and relation | unit + integration |
| DESIGN-REQ-009 | missing | No resume checkpoint request validation | Add checkpoint and edited-input rejection validation | unit + contract |
| DESIGN-REQ-010 | missing | No resume related-run model | Add related-run contract and display | unit + frontend unit |
| DESIGN-REQ-011 | missing | No resume checkpoint evidence persistence | Store checkpoint artifact/ref or durable read-model evidence | unit + integration |
| DESIGN-REQ-012 | missing | No resume checkpoint model | Add checkpoint fields and validation | unit |
| DESIGN-REQ-013 | partial | Step ledger query/projection exists; resume update events absent | Add checkpoint/preserved-step diagnostics and update semantics | unit + integration |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control task details  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, Zod, existing Temporal artifact service/helpers  
**Storage**: Existing Temporal execution records, canonical execution parameters/memo/search attributes, Temporal artifact metadata/content store, and existing workflow history; no new persistent database table is planned unless reverse related-run queries cannot be implemented safely from existing execution records  
**Unit Testing**: `./tools/test_unit.sh` for final unit suite; focused Python `pytest` through the same runner, and `npm run ui:test -- <path>` or `./tools/test_unit.sh --ui-args <path>` for frontend iteration  
**Integration Testing**: `./tools/test_integration.sh` for required `integration_ci` suite; targeted FastAPI/Temporal boundary tests under `tests/contract`, `tests/unit/workflows/temporal`, and compose-backed integration tests where runtime dependencies are required  
**Target Platform**: MoonMind local-first Docker Compose deployment and managed-agent worker/runtime containers  
**Project Type**: Full-stack workflow orchestration web application with Temporal-backed execution engine and Mission Control UI  
**Performance Goals**: Resume eligibility and task-detail reads remain bounded to one source execution plus checkpoint/ref lookups; invalid Resume requests fail before any agent or step execution starts  
**Constraints**: Preserve source failed execution immutability, keep large checkpoint/step content out of workflow history, keep source `workflowId` and `runId` pinned, reject edited resume payloads, and avoid semantic drift with existing Rerun/Edit/paused Resume controls  
**Scale/Scope**: One runtime story covering failed-step Resume for `MoonMind.Run`; out of scope are editable Resume, generic RequestRerun behavior changes beyond distinction, and a full historical per-run product surface

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | Resume stays in MoonMind orchestration contracts and does not rebuild provider agent behavior. |
| II. One-Click Agent Deployment | PASS | Plan uses existing local-first services and test runners; no mandatory external service is introduced. |
| III. Avoid Vendor Lock-In | PASS | Resume is modeled against `MoonMind.Run`, task snapshots, artifacts, and adapter boundaries rather than provider-specific behavior. |
| IV. Own Your Data | PASS | Checkpoint evidence and related metadata remain in MoonMind-controlled artifacts/records. |
| V. Skills Are First-Class and Easy to Add | PASS | No new skill system behavior is required; existing step/runtime boundaries remain composable. |
| VI. Replaceable Scaffolding, Thick Contracts | PASS | Work centers on contracts, validation, and tests; provider/runtime specifics remain behind adapters. |
| VII. Runtime Configurability | PASS | No new hardcoded provider behavior; eligibility is derived from runtime state and existing configuration. |
| VIII. Modular and Extensible Architecture | PASS | Changes are planned across API/service/workflow/UI boundaries with explicit contracts. |
| IX. Resilient by Default | PASS | Resume is a resiliency feature and requires fail-fast validation plus boundary regression coverage. |
| X. Continuous Improvement | PASS | Operator-visible diagnostics and structured outcomes are planned for checkpoint/resume failures. |
| XI. Spec-Driven Development | PASS | `spec.md`, `plan.md`, and design artifacts preserve traceability before implementation. |
| XII. Canonical Docs Desired State | PASS | Migration and rollout details stay in MoonSpec artifacts, not canonical docs. |
| XIII. Pre-Release Velocity | PASS | Plan avoids compatibility aliases for internal contracts; superseded internal paths should be updated directly. |

Post-Phase 1 re-check: PASS. The generated research, data model, contracts, and quickstart preserve the same gates and introduce no justified violations.

## Project Structure

### Documentation (this feature)

```text
specs/310-resume-from-last-failed-step/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── resume-from-failed-step-api.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Created later by /speckit.tasks, not this step
```

### Source Code (repository root)

```text
api_service/
├── api/routers/executions.py          # execution detail/actions, rerun route, planned resume route
└── db/models.py                       # existing execution records and artifact metadata surfaces

moonmind/
├── schemas/temporal_models.py         # execution action, task snapshot, step ledger, planned resume contracts
├── workflows/temporal/service.py      # execution creation/update/rerun orchestration, planned resume service
├── workflows/temporal/step_ledger.py  # step ledger helpers, planned preserved-step status/provenance
└── workflows/temporal/workflows/run.py # MoonMind.Run query/start behavior, planned resume branch

frontend/src/
├── entrypoints/task-detail.tsx        # task detail actions and related-run UI
└── entrypoints/task-detail.test.tsx   # task detail behavior tests

tests/
├── unit/api/routers/test_executions.py
├── unit/workflows/temporal/test_temporal_service.py
├── unit/workflows/temporal/workflows/
├── contract/test_temporal_execution_api.py
└── integration/workflows/temporal/workflows/
```

**Structure Decision**: Use the existing full-stack MoonMind layout. The feature spans the FastAPI execution router, Temporal execution service/workflow contracts, shared Pydantic schemas, artifact-backed checkpoint evidence, and Mission Control task-detail UI. Tests should be added at the same boundaries rather than creating a new subsystem.

## Complexity Tracking

No constitution violations are planned.
