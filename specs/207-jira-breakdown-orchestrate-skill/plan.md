# Implementation Plan: Jira Breakdown and Orchestrate Skill

**Branch**: `207-jira-breakdown-orchestrate-skill` | **Date**: 2026-04-18 | **Spec**: [spec.md](spec.md) 
**Input**: Single-story feature specification from `specs/207-jira-breakdown-orchestrate-skill/spec.md`

**Setup note**: `scripts/bash/setup-plan.sh --json` was attempted, but this checkout does not contain that helper path or a plan template. This plan follows the repository's existing MoonSpec plan output contract manually using the active `.specify/feature.json` directory.

## Summary

Implement MM-404 by adding a reusable Jira Breakdown and Orchestrate workflow surface that composes the existing Jira Breakdown and Jira Orchestrate flows without replacing either one. Repo inspection shows `jira-breakdown` already runs `moonspec-breakdown` and `story.create_jira_issues`, including ordered Jira blocker-link creation. `jira-orchestrate` already implements one Jira-backed story through the MoonSpec lifecycle. Task dependencies already exist through create-time `payload.task.dependsOn` on separate top-level `MoonMind.Run` executions. The missing behavior is a composite orchestration surface that takes created Jira story issues, creates one downstream Jira Orchestrate task per story, wires each later task to depend on the immediately earlier task, and reports partial outcomes honestly. The implementation should be test-first: add unit tests for the new template/tool contract and task creation helper, add integration-style template seeding coverage, then implement the new seeded preset and deterministic trusted task-creation tool/service boundary.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | missing | no `jira-breakdown-orchestrate` preset or tool found | add discoverable reusable workflow surface | unit + integration |
| FR-002 | partial | `jira-breakdown` accepts `feature_request`; composite source input absent | add composite input for source Jira issue or design input | unit |
| FR-003 | partial | `api_service/data/task_step_templates/jira-breakdown.yaml` performs normal breakdown | compose existing breakdown step before downstream orchestration | unit + integration |
| FR-004 | partial | `story.create_jira_issues` preserves issue mappings and order | carry ordered story or issue mappings into downstream task creation | unit |
| FR-005 | missing | no code creates Jira Orchestrate tasks from story mappings | add task creation helper/tool to create one task per generated Jira story | unit + integration |
| FR-006 | partial | Jira issue mappings preserve story IDs and source descriptions | include Jira issue key and source traceability in every downstream task payload | unit |
| FR-007 | partial | task `dependsOn` exists through `/api/executions` and `TemporalExecutionService`; no composite wiring | create downstream tasks sequentially with `dependsOn` pointing to prior task workflow IDs | unit + integration |
| FR-008 | missing | no composite single-story behavior exists | handle one created story with one downstream task and zero dependencies | unit |
| FR-009 | missing | no composite zero-story behavior exists | return no-downstream-task outcome without success overclaiming | unit |
| FR-010 | partial | `story.create_jira_issues` reports partial Jira/link failures | add downstream task/dependency partial result reporting | unit |
| FR-011 | partial | trusted Jira service and story output tool exist; composite path absent | keep Jira operations on existing trusted surfaces and avoid raw credentials | unit + static check |
| FR-012 | implemented_verified | existing `jira-breakdown` preset remains separate and covered by seed tests | no replacement; compose existing behavior | final verify |
| FR-013 | implemented_verified | existing `jira-orchestrate` preset remains separate and covered by seed tests | no replacement; create tasks that run existing behavior | final verify |
| FR-014 | partial | existing Jira Orchestrate delegates implementation to separate runs; composite absent | ensure composite only creates downstream tasks, not inline implementation | unit + integration |
| FR-015 | implemented_unverified | `spec.md` preserves MM-404 and original brief | preserve through plan, tasks, verification, commit, and PR metadata | traceability check |
| SC-001 | missing | no downstream Jira Orchestrate task creation from three stories | prove three stories create three tasks | unit + integration |
| SC-002 | missing | no downstream task dependency wiring exists | prove tasks 2 and 3 depend on previous workflow IDs | unit + integration |
| SC-003 | missing | no single-story composite behavior exists | prove one story creates one task and zero dependencies | unit |
| SC-004 | missing | no zero-story composite behavior exists | prove zero stories creates zero tasks and no-success outcome | unit |
| SC-005 | missing | no downstream partial outcome contract exists | prove partial failures identify successes and failures | unit |
| SC-006 | partial | existing Jira Orchestrate runs separately; composite absent | prove composite does not run implementation inline | unit + integration |
| SC-007 | implemented_unverified | MM-404 preserved in spec and plan | preserve through remaining artifacts and final verification | traceability check |

## Technical Context

**Language/Version**: Python 3.12 with Pydantic v2 and SQLAlchemy async ORM 
**Primary Dependencies**: Existing task template catalog, Temporal execution service, Jira story output tooling, trusted Jira tool service, MoonMind task dependency contract 
**Storage**: Existing task template seed data, existing Temporal execution records, and existing execution dependency edge table; no new persistent database tables planned 
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/temporal/test_story_output_tools.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/temporal/test_temporal_service.py` during iteration; final `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` 
**Integration Testing**: `./tools/test_integration.sh` for required hermetic `integration_ci` coverage when Docker is available; add or extend `tests/integration/test_startup_task_template_seeding.py` for seeded preset persistence 
**Target Platform**: MoonMind API service and Temporal-backed task creation on Linux containers 
**Project Type**: Backend workflow orchestration and seeded task template feature 
**Performance Goals**: Downstream task creation is linear in generated story count, bounded by normal task submission limits, and does not poll or wait for downstream execution completion during creation 
**Constraints**: Runtime mode; no raw Jira credentials in agent runtimes; compose existing Jira Breakdown and Jira Orchestrate behavior; task dependencies are create-time only and target existing `MoonMind.Run` workflow IDs; direct dependencies are limited to 10 per run; dependency graph is linear and non-transitive by contract; preserve MM-404 traceability 
**Scale/Scope**: One source Jira/design input produces zero or more Jira story issues and zero or more downstream Jira Orchestrate tasks; the first version uses linear dependency ordering only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan composes existing agent workflows and task orchestration instead of rebuilding Jira Breakdown or Jira Orchestrate behavior.
- **II. One-Click Agent Deployment**: PASS. No new external service, required secret, or infrastructure dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Jira-specific behavior stays behind existing trusted Jira surfaces and task templates.
- **IV. Own Your Data**: PASS. Task creation and dependency evidence remain in MoonMind execution records and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. The feature adds a reusable orchestration surface while preserving the distinction between executable tools and agent instruction skills.
- **VI. Replaceability and Scientific Method**: PASS. The plan is test-first and keeps the composite workflow replaceable.
- **VII. Runtime Configurability**: PASS. Runtime, repository, project, issue type, and dependency behavior remain submitted inputs rather than hidden constants.
- **VIII. Modular and Extensible Architecture**: PASS. Planned changes stay in seeded template, story output/tool, and task creation service boundaries.
- **IX. Resilient by Default**: PASS with required guard. Creating downstream tasks and dependencies is side-effecting and must be idempotency-aware with partial outcome reporting.
- **X. Facilitate Continuous Improvement**: PASS. The feature records structured downstream task and dependency outcomes.
- **XI. Spec-Driven Development**: PASS. Implementation proceeds from the MM-404 spec, plan, tasks, and verification sequence.
- **XII. Documentation Separation**: PASS. Volatile Jira orchestration input remains under `local-only handoffs`; no canonical migration backlog is added.
- **XIII. Pre-release Compatibility Policy**: PASS. No compatibility aliases are planned; unsupported values fail through validation.

## Project Structure

### Documentation (this feature)

```text
specs/207-jira-breakdown-orchestrate-skill/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│ └── jira-breakdown-orchestrate.md
└── checklists/
 └── requirements.md
```

### Source Code (repository root)

```text
api_service/
├── data/task_step_templates/
│ ├── jira-breakdown.yaml
│ ├── jira-orchestrate.yaml
│ └── jira-breakdown-orchestrate.yaml
├── api/routers/executions.py
└── services/task_templates/catalog.py

moonmind/
├── workflows/temporal/
│ ├── service.py
│ └── story_output_tools.py
└── schemas/temporal_models.py

tests/
├── unit/api/test_task_step_templates_service.py
├── unit/workflows/temporal/test_story_output_tools.py
├── unit/workflows/temporal/test_temporal_worker_runtime.py
├── unit/workflows/temporal/test_temporal_service.py
└── integration/test_startup_task_template_seeding.py
```

**Structure Decision**: Add a new global seeded preset and a narrow deterministic tool/service boundary for creating downstream Jira Orchestrate tasks from ordered Jira story mappings. Reuse existing Jira issue creation and existing task dependency validation instead of adding new tables or replacing existing presets.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
| --- | --- | --- |
| None | N/A | N/A |

## Phase 0: Research Summary

Research classifies MM-404 as a runtime story. Current code already has Jira Breakdown, Jira Orchestrate, trusted Jira story creation, Jira issue-linking, and top-level task dependencies. The missing behavior is downstream task creation from ordered created story issues, plus dependency wiring between those created task workflow IDs. The implementation should avoid template-authored dependency graphs because the current dependency contract is create-time only; instead the composite tool should create tasks sequentially so each dependent task can reference an already-created prerequisite workflow ID.

## Phase 1: Design Artifact Summary

- `research.md`: documents repo evidence, requirement status, composition decisions, and unit/integration test strategy.
- `data-model.md`: defines source input, generated story, Jira story issue mapping, downstream task request/result, dependency edge, and orchestration result.
- `contracts/jira-breakdown-orchestrate.md`: defines the seeded preset and deterministic downstream task creation contract.
- `quickstart.md`: defines test-first validation commands and end-to-end verification scenarios.

## Post-Design Constitution Re-Check

PASS. The design keeps Jira operations behind trusted tooling, preserves existing Jira Breakdown and Jira Orchestrate behavior, creates top-level task dependencies only after prerequisite workflow IDs exist, and records partial outcomes instead of claiming success on incomplete orchestration.

## Managed Setup Note

The active managed runtime branch may not match the numbered feature directory. `.specify/feature.json` points to `specs/207-jira-breakdown-orchestrate-skill`. The documented plan setup and agent-context helper scripts are not present at the paths named in the skill instructions, so this plan was generated manually against the active feature directory.
