# Implementation Plan: Resume Execution Semantics

**Branch**: `change-jira-issue-mm-647-to-status-in-pr-90f53137` | **Date**: 2026-05-13 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:6d4f0168-95fa-48a3-9cb3-f3508f959fd5/repo/specs/346-resume-execution-semantics/spec.md`

**Setup Note**: `scripts/bash/setup-plan.sh --json` was attempted and was not present. `.specify/scripts/bash/setup-plan.sh --json` was then attempted, but the managed job branch name is not in the script's expected `NNN-feature-name` form. Planning proceeds from `.specify/feature.json`, which points to `specs/346-resume-execution-semantics`.

## Summary

MM-647 requires `MoonMind.Run` to consume a validated failed-step Resume checkpoint, restore pre-failed-step execution state, preserve prior completed steps with source provenance, inject preserved outputs into the retried failed step and downstream steps, retry the failed step first, and fail explicitly when restoration cannot be trusted. Current repo evidence shows recovery contract models, service-level checkpoint validation, failed-step Resume submission guards, preserved-step ledger helpers, checkpoint eligibility markers, and some integration coverage already exist. The remaining risk is the real `MoonMind.Run` workflow boundary: workspace restoration before step execution, preserved-output injection into actual step inputs, direct workflow validation of resume source payloads, first-new-step ordering, and fresh evidence production for retried and downstream steps need explicit verification-first coverage and likely implementation work.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `TaskRecoveryProvenance`, `ResumeFromFailedStepRef`, `TemporalExecutionService.create_failed_step_resume_execution()` build source workflow/run refs; API/service tests cover accepted Resume refs | Add workflow-boundary tests proving `MoonMind.Run` receives and records source IDs before execution starts | unit + integration |
| FR-002 | partial | Service validates checkpoint source IDs, snapshot ref, failed step, and plan identity before creating a resumed execution | Add direct `MoonMind.Run` resume-source validation or fail-fast guard so malformed persisted/in-flight payloads cannot bypass service validation | unit + integration |
| FR-003 | partial | Service carries `task.resume.taskInputSnapshotRef`; API rejects edited Resume payload fields | Verify/respect unchanged original task input snapshot at workflow initialization and block direct edited Resume payload drift | unit + integration |
| FR-004 | implemented_unverified | `materialize_preserved_steps()` imports preserved rows; `run.py::_initialize_step_ledger()` applies `resumeSource.preservedSteps` | Add real workflow tests proving preserved prior steps are imported rather than executed | integration |
| FR-005 | implemented_unverified | Preserved ledger rows carry `preservedFrom` with source workflow, run, logical step, and attempt in unit/integration helper tests | Add workflow-boundary assertion that preserved provenance survives `MoonMind.Run` initialization and projection | unit + integration |
| FR-006 | missing | `ResumeSourceModel` carries `resumeWorkspace`, but no workflow code found that materializes workspace/branch/commit state before the failed step starts | Add workspace restoration activity/adapter call or existing boundary integration and tests before failed-step execution | unit + integration |
| FR-007 | implemented_unverified | Task detail diagnostics expose `targetDiagnostics.recovery.preservedSteps`; step ledger rows have `preservedFrom` | Add UI/API projection proof that actual resumed run rows render preserved prior steps as reused | unit + UI/integration |
| FR-008 | partial | Preserved output refs are retained on preserved ledger row artifacts; no proof found that real step input composition consumes them | Add verification-first tests for preserved output injection into failed and downstream step input contracts; implement fallback if missing | unit + integration |
| FR-009 | implemented_unverified | Preserved-step helper refreshes failed step to `ready`; no end-to-end workflow ordering test for first newly executed step | Add workflow ordering test showing preserved prior steps are skipped and failed step is first new attempt | integration |
| FR-010 | implemented_unverified | Step ledger readiness can unblock later steps after failed step; no resumed-run downstream execution proof found | Add resumed-run downstream continuation scenario | integration |
| FR-011 | partial | `mark_step_checkpoint_evidence()` and `run.py` record step refs/checkpoint evidence after step results | Verify retried failed and later steps produce fresh resumed-run ledger rows/artifacts/checkpoints, not copied source evidence | unit + integration |
| FR-012 | partial | Service rejects invalid/mismatched checkpoint payloads before creating resumed execution; route reports checkpoint authorization/stale/inconsistent reasons | Add direct workflow invalid-restoration guards and tests for malformed `resumeSource`, workspace restoration failure, and no full-rerun fallback | unit + integration |
| FR-013 | implemented_verified | `tests/unit/api/routers/test_executions.py::test_failed_step_resume_request_rejects_edited_task_payload_fields`; service/rerun tests strip Resume fields for other recovery paths | No new implementation unless direct workflow payload tests expose drift | final verify + targeted unit if touched |
| FR-014 | implemented_verified | `spec.md` preserves `MM-647` and the original Jira preset brief | Preserve traceability through plan, research, data model, contract, quickstart, tasks, verification, commit, and PR metadata | final verify |
| SCN-001 | partial | Service validation exists before resumed execution creation | Add workflow-boundary checkpoint validation scenario | unit + integration |
| SCN-002 | missing | `resumeWorkspace` is carried but not materialized by workflow code found | Add pre-failed-step workspace restoration scenario | unit + integration |
| SCN-003 | implemented_unverified | Preserved-step helper marks source provenance and skips new attempt | Add real resumed-run initialization scenario | integration |
| SCN-004 | partial | Preserved artifact refs stay on ledger rows; no proof they reach step input composition | Add preserved-output injection scenario | unit + integration |
| SCN-005 | implemented_unverified | Failed step becomes ready after preserved prior step materialization | Add first-new-step ordering scenario | integration |
| SCN-006 | partial | Step result evidence helper records fresh checkpoint refs | Add fresh resumed-run evidence scenario for retried and later steps | unit + integration |
| SCN-007 | partial | Service and route reject invalid checkpoint evidence; workflow direct invalid source not covered | Add explicit invalid-restoration no-fallback scenario | unit + integration |
| SCN-008 | implemented_verified | API route rejects edited Resume payload field categories | Preserve existing behavior and add only if touched | final verify |
| SC-001 | partial | Service tests validate accepted Resume refs; workflow-boundary proof missing | Add workflow validation test | unit + integration |
| SC-002 | missing | Workspace restoration before failed step is not evidenced | Add restoration behavior and tests | unit + integration |
| SC-003 | implemented_unverified | Helper tests prove preserved source provenance and no new attempt | Add workflow-boundary proof and projection assertion | unit + integration |
| SC-004 | partial | Ledger retains preserved outputs; injection into execution inputs unproven | Add input-composition tests | unit + integration |
| SC-005 | partial | Failed-step readiness and fresh evidence helpers exist; full resumed run proof incomplete | Add ordering and fresh-evidence tests | integration |
| SC-006 | partial | Service rejects invalid checkpoint before creation; workflow malformed/restoration-failure no-fallback proof missing | Add workflow no-fallback tests | unit + integration |
| SC-007 | implemented_verified | Edited Resume payload rejection matrix exists | Preserve behavior | final verify |
| SC-008 | implemented_verified | `spec.md` preserves Jira and source IDs | Preserve through downstream artifacts | final verify |
| DESIGN-REQ-001 | partial | Service/route and ledger pieces exist; complete `MoonMind.Run` execution semantics not fully proven | Add workflow-boundary tests and implementation for restoration/injection/order/failure gaps | unit + integration |
| DESIGN-REQ-002 | partial | `MoonMind.Run` initializes preserved rows and records step evidence; workspace materialization and output injection incomplete/unproven | Add workflow restoration and injection behavior | unit + integration |
| DESIGN-REQ-003 | partial | Helper prevents preserved-step re-execution; no workflow no-fallback proof | Add invalid restoration and preserved-step no-reexecute coverage | unit + integration |
| DESIGN-REQ-004 | implemented_verified | Resume payload models and API rejection enforce original input/no-edit contract | Preserve and retest if touched | final verify |
| DESIGN-REQ-005 | implemented_unverified | Ledger rows support `preservedFrom`; UI diagnostics expose recovery | Add projection and workflow evidence tests | unit + integration |
| DESIGN-REQ-006 | implemented_unverified | Task recovery/source ID models and service validation pin both source IDs | Add end-to-end resumed-run source pinning proof | unit + integration |

Status summary: 5 missing, 14 partial, 17 implemented_unverified, 4 implemented_verified.

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic v2, FastAPI execution router/service models, existing Temporal artifact service/helpers  
**Storage**: Existing Temporal workflow history, execution records, memo/search attributes, Temporal artifact metadata/content store, task input snapshot artifacts, resume checkpoint artifacts; no new persistent database table planned  
**Unit Testing**: pytest via `./tools/test_unit.sh`; focused tests under `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/api/routers/test_executions.py`, and step-ledger helper tests  
**Integration Testing**: pytest hermetic integration via `./tools/test_integration.sh`; focused `integration_ci` coverage under `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and `tests/integration/temporal/test_backend_resume_eligibility.py`  
**Target Platform**: MoonMind API service and Temporal worker runtime on Linux containers  
**Project Type**: Python service and Temporal workflow orchestration system  
**Performance Goals**: Resume initialization and validation remain bounded; no large checkpoint or workspace payloads are embedded inline in workflow histories; preserved-step initialization scales linearly with step count  
**Constraints**: Workflow/activity payload shapes are compatibility-sensitive; failed-step Resume must never silently fall back to full rerun; preserved prior steps must not re-execute; direct agent/runtime behavior remains provider-agnostic; checkpoint and workspace refs stay artifact-backed  
**Scale/Scope**: One `MoonMind.Run` execution-semantics story covering validated checkpoint consumption, workspace restoration, preserved-step state, preserved-output injection, failed-step retry ordering, downstream continuation, explicit restoration failure, and traceability for MM-647

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan strengthens MoonMind orchestration of existing runtimes rather than rebuilding agent behavior.
- **II. One-Click Agent Deployment**: PASS. Uses existing Temporal, artifact, and local test infrastructure; no new external service or secret is planned.
- **III. Avoid Vendor Lock-In**: PASS. Resume semantics are workflow/ref based and provider-neutral.
- **IV. Own Your Data**: PASS. Checkpoints, snapshots, preserved outputs, and run evidence remain in MoonMind-controlled records/artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. Work stays in runtime/task boundaries and does not change skill source or materialization semantics.
- **VI. Scientific Method**: PASS. The plan requires verification-first unit and integration coverage before implementation.
- **VII. Runtime Configurability**: PASS. No new hardcoded operator settings are planned; behavior derives from explicit task recovery metadata.
- **VIII. Modular Architecture**: PASS. Planned changes stay in existing schema, service, step-ledger, workflow, and route/projection boundaries.
- **IX. Resilient by Default**: PASS with required tests. The story improves explicit failure, no-fallback behavior, and retry-safe evidence handling.
- **X. Continuous Improvement**: PASS. Final verification will produce structured evidence and preserve residual risks.
- **XI. Spec-Driven Development**: PASS. Planning derives from the single-story spec and preserves all requirements.
- **XII. Documentation Separation**: PASS. Planning details stay under `specs/346-resume-execution-semantics/`, not canonical docs.
- **XIII. Pre-Release Compatibility Policy**: PASS. Avoid compatibility aliases for internal contracts; preserve Temporal worker-bound invocation compatibility or document an explicit cutover if a payload shape must change.

Re-check after Phase 1 design: PASS. `research.md`, `data-model.md`, `contracts/resume-execution.md`, and `quickstart.md` introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/346-resume-execution-semantics/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── resume-execution.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
api_service/
└── api/
    └── routers/
        └── executions.py

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    ├── tasks/
    │   ├── prepared_context.py
    │   └── task_contract.py
    └── temporal/
        ├── service.py
        ├── step_ledger.py
        └── workflows/
            └── run.py

tests/
├── unit/
│   ├── api/routers/test_executions.py
│   └── workflows/
│       ├── tasks/test_task_contract.py
│       └── temporal/
│           ├── test_step_ledger.py
│           ├── test_temporal_service.py
│           └── workflows/test_run_resume_from_failed_step.py
└── integration/
    ├── temporal/test_backend_resume_eligibility.py
    └── workflows/temporal/workflows/test_run_resume_from_failed_step.py
```

**Structure Decision**: Keep implementation in existing Temporal workflow, schema, service, route/projection, prepared-context, and step-ledger modules. Add verification-first tests at helper/model boundaries and the real `MoonMind.Run`/Temporal service boundary before changing production behavior.

## Complexity Tracking

No constitution violations are planned.
