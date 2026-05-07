# Implementation Plan: Normalize Proposal Intent in Temporal Submissions

**Branch**: `run-jira-orchestrate-for-mm-595-normaliz-681cb17e`
**Date**: 2026-05-06
**Spec**: `specs/309-normalize-proposal-intent/spec.md`
**Input**: Single-story feature specification from `specs/309-normalize-proposal-intent/spec.md`

## Summary

MM-595 requires all new task creation paths to persist proposal intent in the canonical nested task payload, while keeping older-shape reads isolated for replay and in-flight safety. Existing API and workflow code already preserve nested `task.proposeTasks` and `task.proposalPolicy`, and proposal-stage gating has unit coverage. The remaining plan is to remove root-level proposal intent from new writes, add boundary tests for API, Temporal, Codex managed-session task creation, schedules/promotions where applicable, and verify proposal state vocabulary across API, UI mapping, finish summaries, and documentation touched by the change.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `api_service/api/routers/executions.py` writes nested `task.proposeTasks` but also root `initial_parameters["proposeTasks"]`; `tests/unit/api/routers/test_executions.py::test_create_task_shaped_execution_preserves_proposal_and_skill_intent` currently asserts both | stop writing root proposal intent for new API submissions and update tests | unit + integration |
| FR-002 | implemented_unverified | `api_service/api/routers/executions.py` preserves `task.proposalPolicy`; API unit test asserts normalized nested policy | keep nested raw policy preservation and add focused regression for no root-level policy write | unit |
| FR-003 | partial | API path normalizes nested payload; `moonmind/agents/codex_worker/worker.py` reads canonical payload; schedules/promotions need targeted inspection during implementation | normalize or prove API, schedule, promotion, and Codex-originated submissions share the same nested contract | unit + integration |
| FR-004 | partial | root `initial_parameters["proposeTasks"]` remains a new-write path; Temporal workflow still reads root as compatibility fallback | remove new root writes and constrain root reads to compatibility-only tests | unit + workflow-boundary |
| FR-005 | partial | `moonmind/workflows/temporal/workflows/run.py::_proposal_generation_requested` reads nested first and falls back to root; tests cover nested and global gate but not explicit compatibility isolation | add replay/in-flight compatibility test and new-write no-root regression | workflow-boundary |
| FR-006 | implemented_unverified | `api_service/db/models.py` has `PROPOSALS`; `api_service/api/routers/task_dashboard_view_model.py` maps `proposals`; `moonmind/workflows/temporal/workflows/run.py` emits proposals finish summary | add consistency checks for workflow/API/UI mapping/finish summary and update docs only if touched | unit + integration |
| FR-007 | implemented_verified | `tests/unit/workflows/temporal/workflows/test_run_proposals.py` covers nested `task.proposeTasks` and global disable gate | preserve behavior while removing root writes | existing unit + final verify |
| FR-008 | implemented_unverified | `spec.md` preserves MM-595 and canonical Jira preset brief | preserve traceability in plan, tasks, verification, commit, and PR metadata | traceability |
| SC-001 | partial | API test shows nested fields but also root field | update submission assertions across representative surfaces | unit + integration |
| SC-002 | implemented_verified | `tests/unit/workflows/temporal/workflows/test_run_proposals.py::test_run_proposals_stage_skipped_when_globally_disabled` and nested opt-in test | retain and extend as needed for no-root behavior | unit |
| SC-003 | partial | compatibility fallback exists but is not isolated by an explicit replay-style regression | add compatibility-specific workflow-boundary test | workflow-boundary |
| SC-004 | partial | root API write contradicts desired no-root output | remove root write and add regression | unit |
| SC-005 | implemented_unverified | state vocabulary exists in workflow, DB enum, dashboard mapping, and finish summary | add status vocabulary consistency test and touch docs only as needed | unit + integration |
| SC-006 | implemented_unverified | `spec.md` preserves MM-595 and DESIGN-REQ mappings | keep evidence through planning, tasks, verification | traceability |
| DESIGN-REQ-003 | partial | nested policy is preserved, but root proposal flag still persists | remove root proposal intent writes | unit |
| DESIGN-REQ-004 | partial | API and Codex paths partially align; schedules/promotions need proof | add cross-surface normalization tests and implementation where gaps are found | unit + integration |
| DESIGN-REQ-005 | partial | older-shape reads exist in workflow fallback; new writes still include root flag | isolate compatibility reads and remove root new-write output | workflow-boundary |
| DESIGN-REQ-006 | implemented_unverified | `proposals` vocabulary appears in workflow state, API allowed states, dashboard mapping, and finish summaries | add consistency tests and docs touch-up if implementation changes references | unit + integration |

## Technical Context

- Language/version: Python 3.12; TypeScript/React only if Mission Control status mapping changes
- Primary dependencies: FastAPI, Pydantic v2, Temporal Python SDK, SQLAlchemy async ORM, existing task contract models, existing React/Vitest test harness
- Storage: existing execution records, Temporal workflow parameters/history, proposal delivery tables, and artifact-backed finish summaries; no new tables planned
- Unit testing: `./tools/test_unit.sh` with targeted pytest paths during iteration
- Integration testing: `./tools/test_integration.sh` for hermetic `integration_ci`; targeted Temporal workflow boundary tests where local runtime duration permits
- Target platform: MoonMind API service, Temporal workflow runtime, managed Codex task creation path, Mission Control status projection
- Project type: backend workflow/API contract change with possible UI status mapping validation
- Performance goals: no additional external calls during task submission or proposal gating; status consistency checks remain local
- Constraints: preserve in-flight/replay safety for older payloads; do not introduce compatibility aliases for new writes; no raw credentials; keep proposal policy resolution behind existing proposal services
- Scale/scope: one task submission or workflow run at a time; representative coverage for API, schedule/promotion, Codex managed-session creation, Temporal proposal gating, and status surfaces

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. The plan preserves provider-agnostic orchestration and adjusts MoonMind's durable run contract.
- II. One-Click Agent Deployment: PASS. No new deployment services or required secrets.
- III. Avoid Vendor Lock-In: PASS. Proposal intent remains a portable task payload contract, not a provider-specific side channel.
- IV. Own Your Data: PASS. Proposal intent stays in operator-owned run parameters and artifacts.
- V. Skills Are First-Class: PASS. Skill and preset provenance behavior is preserved; proposal policy does not redefine skill selection.
- VI. Replaceable Scaffolding: PASS. The plan removes duplicate proposal-intent write paths and strengthens contracts/tests.
- VII. Runtime Configurability: PASS. Global proposal enablement remains configuration-controlled and observable.
- VIII. Modular Architecture: PASS. Work stays in API normalization, task contract/workflow boundaries, managed runtime adapter edges, and status projection.
- IX. Resilient by Default: PASS. Adds workflow-boundary and compatibility coverage for in-flight/replay behavior.
- X. Continuous Improvement: PASS. Keeps proposal generation deterministic and traceable for future improvement workflows.
- XI. Spec-Driven Development: PASS. This plan follows `spec.md` and preserves MM-595 traceability.
- XII. Canonical Docs Separation: PASS. Migration and execution notes remain in feature artifacts; canonical docs are touched only for desired-state vocabulary if needed.
- XIII. Pre-Release Compatibility: PASS. New-write legacy roots are removed rather than aliased; compatibility reads are limited to in-flight/replay safety.

## Project Structure

```text
api_service/
  api/routers/executions.py
  api/routers/task_dashboard_view_model.py
moonmind/
  agents/codex_worker/worker.py
  workflows/tasks/task_contract.py
  workflows/temporal/workflows/run.py
frontend/src/
  utils/executionStatusPillClasses.ts
tests/
  unit/api/routers/test_executions.py
  unit/api/routers/test_task_dashboard_view_model.py
  unit/agents/codex_worker/test_worker.py
  unit/workflows/temporal/workflows/test_run_proposals.py
  integration/workflows/temporal/workflows/test_run.py
docs/Tasks/
  TaskProposalSystem.md
specs/309-normalize-proposal-intent/
  spec.md
  plan.md
  research.md
  data-model.md
  quickstart.md
  contracts/
```

**Structure Decision**: Use the existing API, workflow, managed-runtime, and dashboard modules. Add tests at the same boundaries already covering task-shaped execution, proposal gating, Codex task proposal behavior, and status projection.

## Complexity Tracking

No constitution violations or complexity exceptions.
