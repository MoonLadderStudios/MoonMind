# Implementation Plan: Target-Aware Step Execution Scope

**Branch**: `change-jira-issue-mm-649-to-status-in-pr-179b6dcb` | **Date**: 2026-05-13 | **Spec**: `specs/348-target-aware-step-scope/spec.md`
**Input**: Single-story feature specification from `specs/348-target-aware-step-scope/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but it stopped because the current branch name does not match the numeric MoonSpec feature prefix. Planning continues manually against the active feature directory recorded in `.specify/feature.json`.

## Summary

MM-649 requires step execution and delegated MoonMind.AgentRun child workflows to consume objective context plus only the current step's prepared context. Current repo evidence shows compact prepared-context models, target filtering, parent workflow request assembly, and unit/integration coverage already exist for the main runtime boundary. Planned work is primarily verification-first: strengthen child-workflow authority/diagnostic evidence where current tests are thinner, then patch the parent request metadata or child diagnostic handling only if those verification tests expose a gap.

## Requirement Status

Traceability note: `DESIGN-REQ-001` maps the original Jira coverage ID `DESIGN-REQ-021`, and `DESIGN-REQ-002` maps the original Jira coverage ID `DESIGN-REQ-022`.

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_verified | `moonmind/workflows/temporal/workflows/run.py` builds prepared context before dispatch; `tests/unit/workflows/temporal/workflows/test_run_target_aware_inputs.py` asserts objective + current step refs | Preserve behavior | unit + integration final verify |
| FR-002 | implemented_verified | `moonmind/workflows/tasks/prepared_context.py` selects only matching `stepRef`; unit tests exclude `artifact-step-2` / `report-notes` | Preserve behavior | unit + integration final verify |
| FR-003 | implemented_unverified | Prepared refs include target-specific workspace paths, but same-workspace exclusion evidence should be made explicit | Add verification test first; patch context selection only if it fails | unit + integration |
| FR-004 | implemented_verified | `select_step_prepared_context()` matches explicit `step_ref`; reorder/text edit unit coverage exists in `test_prepared_context.py` | Preserve behavior | unit final verify |
| FR-005 | implemented_verified | Parent `_build_agent_execution_request()` injects prepared context into the request used for `MoonMind.AgentRun`; unit test covers represented-step child request | Preserve behavior | unit + integration final verify |
| FR-006 | implemented_unverified | Parent workflow constructs child request and metadata, but authority semantics need a focused assertion | Add verification test for parent-owned target binding metadata; patch metadata only if missing | unit + integration |
| FR-007 | implemented_unverified | Child diagnostics currently preserve `moonmind.preparedContext` metadata, but no test proves diagnostics cannot redefine target semantics | Add verification test for diagnostic metadata shape; patch child result enrichment only if needed | unit |
| FR-008 | implemented_verified | Unit and integration tests assert unrelated step refs are absent from request payloads | Preserve behavior | unit + integration final verify |
| FR-009 | missing | Spec preserves MM-649; downstream implementation notes, tasks, verification, commit, and PR metadata do not exist yet | Preserve traceability through all later artifacts | final verify |
| SCN-001 | implemented_verified | First-step request includes objective and first-step refs only | Preserve behavior | unit final verify |
| SCN-002 | implemented_unverified | Filtering works with a single manifest, but same-workspace materialization should be asserted by contract | Add explicit same-workspace/materialized-path verification | unit + integration |
| SCN-003 | implemented_verified | `test_child_agent_run_request_receives_only_represented_step_context` covers child request scoping | Preserve behavior | unit + integration final verify |
| SCN-004 | implemented_unverified | Diagnostics metadata exists but target-binding non-redefinition needs focused proof | Add diagnostic authority verification | unit |
| SCN-005 | implemented_unverified | Invalid context is rejected in unit and integration tests; current-step/objective association should remain covered in final verification | Preserve and verify | unit + integration final verify |
| SC-001 | implemented_verified | Unit tests inspect runtime contexts and exclude other step refs | Preserve behavior | unit final verify |
| SC-002 | implemented_unverified | Unit child-request proof exists; add integration/boundary evidence for child workflow input path | Add verification test first | integration |
| SC-003 | implemented_unverified | Current tests imply shared manifest filtering; explicit same-workspace case still planned | Add same-workspace verification | unit + integration |
| SC-004 | implemented_unverified | Parent metadata exists; diagnostic authority proof planned | Add diagnostic verification | unit |
| SC-005 | missing | Final verification does not exist yet | Preserve traceability and run final verify later | final verify |
| DESIGN-REQ-001 | implemented_verified | Step execution filtering code and tests cover objective + current step only | Preserve behavior | unit + integration final verify |
| DESIGN-REQ-002 | implemented_unverified | AgentRun request scoping exists; child logs/diagnostics authority proof still needs targeted evidence | Add verification tests before any patch | unit + integration |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, pytest, existing MoonMind workflow/task contract helpers  
**Storage**: Existing workflow history and artifact refs only; no new persistent storage planned  
**Unit Testing**: `./tools/test_unit.sh`, with focused iteration through pytest targets under `tests/unit/workflows/tasks/` and `tests/unit/workflows/temporal/workflows/`  
**Integration Testing**: `./tools/test_integration.sh`, with focused `integration_ci` coverage under `tests/integration/workflows/temporal/workflows/`  
**Target Platform**: MoonMind Temporal worker/runtime environment on Linux containers  
**Project Type**: Python orchestration service with Temporal workflows and managed/external agent adapters  
**Performance Goals**: Prepared context selection remains bounded to compact refs and metadata; no binary or large generated context enters workflow-visible payloads  
**Constraints**: Maintain Temporal workflow payload compatibility, preserve parent-owned target binding semantics, avoid raw credentials or binary payloads in metadata, keep workflow code deterministic  
**Scale/Scope**: One runtime story covering step request assembly, AgentRun child input scoping, and diagnostic evidence for target-aware prepared context

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Plan keeps provider agents behind existing AgentRun request contracts.
- II. One-Click Agent Deployment: PASS. No new mandatory services, secrets, or deployment dependencies.
- III. Avoid Vendor Lock-In: PASS. Context scoping remains runtime-neutral metadata and refs.
- IV. Own Your Data: PASS. Prepared context uses MoonMind-owned artifact refs and step-level injection.
- V. Skills Are First-Class and Easy to Add: PASS. No skill contract changes are planned.
- VI. Replaceable Scaffolding, Thick Contracts: PASS. Contract tests anchor workflow/request behavior without adding cognitive scaffolding.
- VII. Powerful Runtime Configurability: PASS. No hardcoded provider behavior or new runtime settings.
- VIII. Modular and Extensible Architecture: PASS. Planned work stays in task prepared-context and workflow boundary modules.
- IX. Resilient by Default: PASS. Any workflow/activity payload changes require boundary coverage; current plan is verification-first.
- X. Facilitate Continuous Improvement: PASS. Later verification must produce structured evidence.
- XI. Spec-Driven Development: PASS. `spec.md` exists and this plan preserves requirement traceability.
- XII. Canonical Documentation Separation: PASS. Planning remains in `specs/348-target-aware-step-scope/`.
- XIII. Pre-Release Velocity: PASS. If verification reveals stale aliases or obsolete paths, tasks must remove them rather than preserving wrappers.

## Project Structure

### Documentation (this feature)

```text
specs/348-target-aware-step-scope/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── step-context-scope.md
└── tasks.md             # Created later by /speckit.tasks
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── tasks/
│   │   └── prepared_context.py
│   └── temporal/
│       ├── activity_runtime.py
│       └── workflows/
│           ├── run.py
│           └── agent_run.py
└── schemas/
    └── agent_runtime_models.py

tests/
├── unit/
│   └── workflows/
│       ├── tasks/test_prepared_context.py
│       └── temporal/workflows/test_run_target_aware_inputs.py
└── integration/
    └── workflows/temporal/workflows/test_run_target_aware_inputs.py
```

**Structure Decision**: This is a Python Temporal workflow/runtime contract change. Planning targets existing workflow, task-contract, and test directories rather than creating new services or storage layers.

## Complexity Tracking

No constitution violations requiring complexity tracking.
