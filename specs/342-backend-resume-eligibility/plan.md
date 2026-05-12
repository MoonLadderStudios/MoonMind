# Implementation Plan: Backend-Computed Resume Eligibility

**Branch**: `change-jira-issue-mm-643-to-status-in-pr-bda14b96` | **Date**: 2026-05-12 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/342-backend-resume-eligibility/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed job branch name is not in the script's expected `NNN-feature-name` form. Planning proceeds from the active feature directory `specs/342-backend-resume-eligibility` recorded in `.specify/feature.json`.

## Summary

MM-643 requires failed task details and recovery submissions to treat Edit task, Rerun, and Resume as separate backend-governed recovery intents. The repository already has action capability serialization, Task Detail UI handling, resume checkpoint hydration, route-level rejection of edited Resume payload fields, and recovery/resume contract models. The main delivery risk is that the accepted Resume path uses execution-level `resumeSource` metadata while the spec requires recovery provenance and a failed-step resume reference, and current eligibility evidence appears to be assembled from memo/search-attribute refs rather than a single verified evidence contract. The implementation should start with unit and hermetic integration tests, then tighten backend eligibility, submission normalization, and UI contract assertions only where those tests expose gaps.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `api_service/api/routers/executions.py` returns `canEditForRerun`, `canRerun`, and `canResumeFromFailedStep`; `frontend/src/entrypoints/task-detail.tsx` renders separate actions | Add explicit MM-643 API/UI tests for the full three-action matrix; implementation contingency if matrix gaps appear | unit + integration |
| FR-002 | implemented_unverified | Resume availability is computed in `_build_action_capabilities()` and Task Detail reads `actions.canResumeFromFailedStep` | Add tests proving Mission Control uses backend fields and does not infer Resume from generic status or label text | unit + UI |
| FR-003 | implemented_unverified | `ExecutionActionCapabilityModel` exposes per-execution action fields and disabled reasons | Add contract/unit tests for complete per-execution capability payload including Edit task, Rerun, and Resume unavailable reasons | unit + integration |
| FR-004 | implemented_unverified | `_full_rerun_parameters()` strips recovery carryover; existing tests cover rerun omitting `resumeSource` | Add tests for generic rerun with partial Resume-shaped data and edited full retry to prove neither becomes Resume | unit + integration |
| FR-005 | partial | `TaskRecoveryProvenance` exists in `moonmind/workflows/tasks/task_contract.py`, but Resume creation currently records execution-level `resumeSource` metadata rather than canonical task `recovery` provenance | Normalize accepted recovery submissions to carry or derive recovery provenance consistently, or document the exact execution boundary where provenance is represented | unit + integration |
| FR-006 | partial | `ResumeFromFailedStepRef` exists; `create_failed_step_resume_execution()` records `resumeSource` with source IDs, failed step, checkpoint, workspace, preserved steps, and plan fields | Ensure accepted Resume submissions expose the required failed-step resume reference shape at the relevant task/execution boundary | unit + integration |
| FR-007 | partial | `_resume_evidence_disabled_reason()` requires checkpoint, failed-step id, completed-step refs, workspace checkpoint, and plan identity; snapshot is separately required | Add verification for source workflow ID, source run ID, ledger-derived failed step, completed-step ref completeness, and plan/workspace evidence consistency | unit + integration |
| FR-008 | partial | Missing evidence and checkpoint authorization/corruption/inconsistency return bounded reasons; stale evidence is not clearly represented | Add a complete rejection matrix including missing, stale, unauthorized, inconsistent, corrupted, and plan/workspace mismatch cases | unit + integration |
| FR-009 | implemented_unverified | `resume_execution_from_failed_step()` rejects edited task payload fields with `resume_payload_not_allowed`; Task Detail sends only checkpoint/operator metadata | Add focused tests for all forbidden mutation field categories and UI behavior that routes edits to Edit task | unit + UI |
| FR-010 | implemented_unverified | `spec.md` preserves MM-643 and the original Jira preset brief | Preserve MM-643 through plan, research, data model, contract, quickstart, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | implemented_unverified | API and UI can expose all three actions when capability fields are true | Add test matrix for all action combinations | unit + UI |
| SCN-002 | implemented_unverified | Missing required evidence produces disabled reasons in `test_describe_execution_requires_complete_resume_evidence` | Extend coverage to source workflow/run and stale evidence | unit |
| SCN-003 | partial | Route rejects unauthorized/corrupted/mismatched checkpoint evidence; not all stale/inconsistent cases are covered | Add full pre-execution rejection matrix and assert no resumed execution is created | unit + integration |
| SCN-004 | implemented_unverified | Service full rerun tests strip `resumeSource`; integration covers exact rerun omitting Resume progress | Add generic rerun with partial Resume-shaped data coverage | unit + integration |
| SCN-005 | partial | Resume service builds `resumeSource`; canonical task `recovery`/`resume` payload shape is not consistently proven | Add submission normalization tests for recovery provenance and Resume reference fields | unit + integration |
| SCN-006 | implemented_unverified | Route rejects edited task fields on Resume | Broaden forbidden-field coverage and UI edit-vs-resume tests | unit + UI |
| SC-001 | implemented_unverified | Backend action fields drive Task Detail buttons | Add 100% matrix validation for tested states | unit + UI |
| SC-002 | partial | Missing evidence and checkpoint errors have reasons; stale reason needs explicit handling | Add invalid-evidence reason matrix | unit + integration |
| SC-003 | partial | Resume service records source IDs, failed step, checkpoint, workspace, preserved steps, and plan fields in `resumeSource` | Verify accepted Resume carries every required reference in canonical boundary output | unit + integration |
| SC-004 | implemented_unverified | Rerun sanitization removes Resume metadata | Add generic rerun and edited retry negative tests | unit + integration |
| SC-005 | implemented_unverified | Resume route forbids edited task payload fields | Add field-category matrix and UI routing tests | unit + UI |
| SC-006 | implemented_unverified | `spec.md` and this plan preserve MM-643 and source coverage IDs | Preserve traceability through all downstream artifacts and final verification | final verify |
| DESIGN-REQ-001 | implemented_unverified | Distinct workflows and unavailable reasons are represented in API/UI | Add full matrix proof and no-inference assertions | unit + integration |
| DESIGN-REQ-002 | partial | Backend eligibility checks key evidence categories; source ID and ledger completeness need stronger proof | Tighten or verify complete backend evidence evaluation | unit + integration |
| DESIGN-REQ-003 | partial | Contract types exist; runtime Resume creation uses `resumeSource` instead of canonical task `recovery`/`resume` pair | Align or document boundary representation and add tests | unit + integration |
| DESIGN-REQ-004 | implemented_unverified | Rerun sanitization and route separation prevent obvious generic Resume inference | Add negative tests for partial Resume-shaped rerun payloads | unit + integration |
| DESIGN-REQ-005 | partial | Resume route forbids edits and service pins source workflow/run; checkpointed progress and preserved-step evidence require more proof | Add evidence and no-edit validation matrix | unit + integration |

Status summary: 7 partial, 20 implemented_unverified, 0 missing, 0 implemented_verified.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI behavior  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, Zod, Vitest, pytest  
**Storage**: Existing Temporal execution records, memo/search attributes, Temporal artifact metadata/content store, task input snapshot artifacts, resume checkpoint artifacts; no new persistent database table planned  
**Unit Testing**: `./tools/test_unit.sh`; focused Python tests in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/tasks/test_task_contract.py`, and `tests/unit/workflows/temporal/test_temporal_service.py`; focused UI tests through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`  
**Integration Testing**: `./tools/test_integration.sh`; add hermetic `integration_ci` coverage in `tests/integration/temporal/` or `tests/integration/workflows/temporal/` for execution-detail and recovery-submission boundaries  
**Target Platform**: MoonMind API service, Temporal execution service, and Mission Control task detail UI in the existing containerized deployment  
**Project Type**: Web service plus frontend dashboard with Temporal-backed orchestration  
**Performance Goals**: Eligibility calculation remains bounded for task-detail polling; Resume submission validation fails before creating work when evidence is invalid  
**Constraints**: Preserve in-flight Temporal invocation compatibility or document an explicit cutover; keep large skill/content payloads out of workflow history; do not add hidden full-rerun fallback; use existing artifact refs for checkpoint evidence; preserve source execution immutability  
**Scale/Scope**: One runtime story for `MoonMind.Run` failed-task recovery action eligibility and submission validation; excludes executing resumed steps beyond the accepted Resume handoff contract

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The work strengthens MoonMind recovery orchestration around existing agents.
- **II. One-Click Agent Deployment**: PASS. No new external service, secret, or deployment prerequisite is planned.
- **III. Avoid Vendor Lock-In**: PASS. Recovery state is MoonMind-owned orchestration metadata and artifacts.
- **IV. Own Your Data**: PASS. Resume evidence remains in local execution records and artifact stores.
- **V. Skills Are First-Class and Easy to Add**: PASS. No agent skill runtime changes are planned.
- **VI. Scaffolds Are Replaceable, Tests Are the Anchor**: PASS. The plan requires tests first around recovery contracts and failure behavior.
- **VII. Powerful Runtime Configurability**: PASS. Existing task-editing feature flags and action capability surfaces remain observable.
- **VIII. Modular and Extensible Architecture**: PASS. Planned work stays inside existing API, service, schema, and UI boundaries.
- **IX. Resilient by Default**: PASS. The story improves explicit recovery failure behavior and requires boundary tests.
- **X. Facilitate Continuous Improvement**: PASS. Artifacts preserve traceability and evidence for final verification.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Planning derives from the single-story spec and keeps all requirements visible.
- **XII. Documentation Separation**: PASS. Migration and execution details stay in `specs/342-backend-resume-eligibility/`.
- **XIII. Pre-Release Compatibility Policy**: PASS. Any internal contract tightening should remove stale shapes in the same change unless Temporal payload compatibility requires a documented cutover.

Re-check after Phase 1 design: PASS. `research.md`, `data-model.md`, `contracts/recovery-eligibility.md`, and `quickstart.md` introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/342-backend-resume-eligibility/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── recovery-eligibility.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── executions.py

moonmind/
└── workflows/
    ├── tasks/
    │   └── task_contract.py
    └── temporal/
        └── service.py

frontend/
└── src/
    └── entrypoints/
        ├── task-detail.tsx
        └── task-detail.test.tsx

tests/
├── unit/
│   ├── api/routers/test_executions.py
│   ├── workflows/tasks/test_task_contract.py
│   └── workflows/temporal/test_temporal_service.py
├── contract/
│   └── test_temporal_execution_api.py
└── integration/
    ├── api/
    └── temporal/
```

**Structure Decision**: Use the existing execution detail route, failed-step Resume route, Temporal execution service, task contract models, and Task Detail UI. Add verification-first tests at API/service/task-contract/UI boundaries, then implement only the gaps exposed by those tests.

## Complexity Tracking

No constitution violations require justification.
