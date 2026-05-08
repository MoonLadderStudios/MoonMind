# Implementation Plan: Execute Resume From the Failed Step Only

**Branch**: `run-jira-orchestrate-for-mm-634-execute-r-7bdf3ad6` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/328-execute-resume-failed-step/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was not used because this managed job branch name is not in the script's expected `NNN-feature-name` form. Planning proceeds from the active feature directory `specs/328-execute-resume-failed-step`.

## Summary

MM-634 requires failed-step Resume execution to reuse the original task input snapshot, validate checkpoint identity before execution, restore runtime state before the failed step, materialize completed prior steps as preserved source progress, retry the failed step first, and continue later steps normally without falling back to full rerun. Current code already has Resume request/response models, checkpoint payload validation, linked resumed execution creation, preserved-step materialization, and Task Detail display for preserved rows. The main gaps are boundary proof that workspace restoration is actually performed before the failed step, preserved provenance includes logical step identity, preserved outputs are used as continuous-run inputs, and invalid restoration cannot re-execute preserved steps or degrade to full rerun.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `TemporalExecutionService.create_failed_step_resume_execution()` carries source snapshot refs in `resumeSource`; Task Detail renders Resume action without opening Create authoring UI | Add tests proving Resume submission accepts only checkpoint refs and never opens or submits an editable authoring payload | unit + UI |
| FR-002 | implemented_unverified | Service validates checkpoint workflow ID, run ID, task snapshot, and plan ref/digest before creating the resumed execution | Add explicit pre-execution boundary tests for all identity mismatches | unit + integration |
| FR-003 | implemented_unverified | Service raises `TemporalExecutionResumeCheckpointError` before `create_execution()` for invalid checkpoint payloads; unit tests cover selected invalid evidence | Add complete restoration-failure matrix and assert no new execution is created | unit + integration |
| FR-004 | missing | `ResumeCheckpointModel.resume_workspace` requires evidence, but no current execution-boundary proof shows workspace/branch/commit restoration before the failed step | Implement or expose runtime restoration from `resumeWorkspace` before first new step and test ordering | unit + integration |
| FR-005 | implemented_unverified | `materialize_preserved_steps()` marks matching initial ledger rows as preserved and refreshes dependencies | Add end-to-end resumed-run test proving prior completed steps are not executed again | integration |
| FR-006 | partial | Preserved rows carry source workflow ID, run ID, and attempt, but not the preserved logical step ID in `preservedFrom` | Add logical step ID to preserved provenance and update schema/tests/UI expectations if needed | unit + integration |
| FR-007 | partial | Preserved artifact refs are copied onto ledger rows, but continuous-run output injection into failed/downstream step context is not proven | Add workflow/context tests proving failed and downstream steps receive preserved outputs | unit + integration |
| FR-008 | partial | `refresh_ready_steps()` can unblock the failed step after preserved rows are materialized; integration tests cover a two-step ledger helper | Add workflow-level assertion that the failed step is the first newly executed step | integration |
| FR-009 | implemented_unverified | Normal workflow progression should execute later ready steps after the failed step succeeds | Add resumed-run scenario proving later steps produce fresh resumed-run ledgers/artifacts/checkpoints | integration |
| FR-010 | implemented_unverified | Invalid checkpoint validation errors before resumed execution creation; no full-rerun call path is present in the service method | Add explicit no-full-rerun/no-created-execution assertions for restoration failure cases | unit + integration |
| FR-011 | partial | Preserved rows are materialized with attempt `0`, but no workflow boundary test proves preserved source rows are skipped by execution | Add a resumed-run test that preserved steps never enter running/executed states | integration |
| FR-012 | implemented_unverified | Task Detail parses and renders `preservedFrom` for step rows | Add UI/API tests ensuring preserved rows are distinguished from newly executed resumed-run rows | unit + UI |
| FR-013 | implemented_unverified | `spec.md` preserves MM-634 and this plan preserves source coverage | Preserve MM-634 through research, data model, contract, quickstart, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SC-001 | implemented_unverified | Resume source carries original snapshot ref | Add authoring-form exclusion tests | unit + UI |
| SC-002 | implemented_unverified | Service validates source and plan identity | Expand mismatch matrix | unit + integration |
| SC-003 | missing | Resume workspace evidence is validated but not restored under tested execution ordering | Add runtime state restoration test before failed step | unit + integration |
| SC-004 | partial | Preserved provenance lacks logical step ID | Add logical step provenance and tests | unit + integration |
| SC-005 | partial | Preserved artifact refs are copied but not proven as execution inputs | Add context/output injection tests | unit + integration |
| SC-006 | partial | Failed step becomes ready in helper tests | Add first-newly-executed-step workflow test | integration |
| SC-007 | implemented_unverified | Invalid checkpoint validation errors before resumed execution creation | Add no-fallback/no-reexecution assertions | unit + integration |
| SC-008 | implemented_unverified | `spec.md` and this plan preserve MM-634 and source IDs | Preserve traceability through all artifacts and final verification | final verify |
| DESIGN-REQ-001 | partial | Distinct Resume workflow exists; no editable UI is present; restoration failure behavior needs broader proof | Strengthen boundary tests and restoration failure handling | unit + integration |
| DESIGN-REQ-002 | partial | Source pinning, checkpoint model, and preserved progress exist; restored state and output injection are incomplete or unverified | Implement/test restoration and continuous-output injection | unit + integration |
| DESIGN-REQ-003 | partial | `MoonMind.Run` initializes preserved ledger rows from `resumeSource`; restoration and fresh downstream checkpointing need proof | Add workflow-boundary tests and code as needed | integration |
| DESIGN-REQ-004 | partial | Explicit recovery intent and no-fallback service behavior exist; no-reexecution proof is incomplete | Add preserved-step no-execution assertions | integration |
| DESIGN-REQ-005 | partial | `MoonMind.Run` consumes resume source and materializes preserved steps; checkpoint durability/restoration ownership needs proof | Add `MoonMind.Run` boundary coverage | integration |

Status summary: 2 missing, 12 partial, 12 implemented_unverified, 0 implemented_verified.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI where preserved-step display changes
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, Zod, Vitest, pytest
**Storage**: Existing Temporal execution records, memo/search attributes, Temporal artifact metadata/content store, task input snapshot artifacts, step ledger/checkpoint artifacts; no new persistent database table planned
**Unit Testing**: `./tools/test_unit.sh`; focused Python tests under `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, and `tests/unit/api/routers/test_executions.py`; focused UI tests through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` if display shape changes
**Integration Testing**: `./tools/test_integration.sh`; targeted hermetic coverage under `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`
**Target Platform**: MoonMind API service, `MoonMind.Run` workflow, Temporal execution service, and Mission Control task detail surface in the existing containerized deployment
**Project Type**: Web service plus frontend dashboard with Temporal-backed orchestration
**Performance Goals**: Resume startup remains bounded by checkpoint validation and state restoration; preserved refs stay compact and avoid large inline workflow payloads
**Constraints**: Preserve in-flight Temporal payload compatibility or document explicit cutover; fail before executing on invalid restoration; never silently fall back to full rerun; never re-execute preserved prior steps without explicit future user intent
**Scale/Scope**: One runtime story for `MoonMind.Run` failed-step Resume execution; excludes backend eligibility gating already covered by MM-633 except where execution requires validated checkpoint evidence.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The story strengthens MoonMind orchestration around existing agents rather than creating a new agent engine.
- **II. One-Click Agent Deployment**: PASS. No new external service, secret, or deployment prerequisite is planned.
- **III. Avoid Vendor Lock-In**: PASS. Resume execution state is MoonMind workflow state, not provider-specific behavior.
- **IV. Own Your Data**: PASS. Resume evidence remains in MoonMind-owned records and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime contract changes are planned.
- **VI. Scaffolds Are Replaceable, Tests Are the Anchor**: PASS. The plan requires boundary tests before implementation changes.
- **VII. Powerful Runtime Configurability**: PASS. No hardcoded external configuration is introduced.
- **VIII. Modular and Extensible Architecture**: PASS. Planned work stays inside existing schema, service, workflow, ledger, API, and UI boundaries.
- **IX. Resilient by Default**: PASS. The story improves deterministic recovery and no-fallback behavior.
- **X. Facilitate Continuous Improvement**: PASS. Artifacts preserve outcome and traceability for verification.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Planning derives from the single-story spec.
- **XII. Documentation Separation**: PASS. Planning artifacts stay under `specs/328-execute-resume-failed-step/`.
- **XIII. Pre-Release Compatibility Policy**: PASS. Internal contracts may be tightened, with compatibility-sensitive Temporal payload changes covered by tests or explicit cutover notes.

Re-check after Phase 1 design: PASS. `research.md`, `data-model.md`, `contracts/resume-execution.md`, and `quickstart.md` introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/328-execute-resume-failed-step/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── resume-execution.md
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
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        ├── service.py
        ├── step_ledger.py
        └── workflows/
            └── run.py

frontend/
└── src/
    └── entrypoints/
        ├── task-detail.tsx
        └── task-detail.test.tsx

tests/
├── unit/
│   ├── api/routers/test_executions.py
│   └── workflows/temporal/
│       ├── test_temporal_service.py
│       └── workflows/test_run_resume_from_failed_step.py
└── integration/
    └── workflows/temporal/workflows/test_run_resume_from_failed_step.py
```

**Structure Decision**: Use the existing Resume API, Temporal service, checkpoint schema, step ledger, `MoonMind.Run` workflow initialization, and Task Detail display surfaces. Add tests first around execution ordering, restored state, preserved provenance, and no re-execution before production changes.

## Complexity Tracking

No constitution violations require justification.
