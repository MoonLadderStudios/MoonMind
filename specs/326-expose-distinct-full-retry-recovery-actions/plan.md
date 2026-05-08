# Implementation Plan: Expose Distinct Full Retry Recovery Actions

**Branch**: `run-jira-orchestrate-for-mm-632-expose-d-a4876f4e` | **Date**: 2026-05-08 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/326-expose-distinct-full-retry-recovery-actions/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed job branch name is not in the script's expected `NNN-feature-name` form. Planning proceeds from `.specify/feature.json`, which points to `specs/326-expose-distinct-full-retry-recovery-actions`.

## Summary

MM-632 requires failed task recovery to expose three distinct intents: Edit task for editable from-beginning retry, Rerun for exact from-beginning retry with original input unchanged, and Resume for failed-step recovery only when durable progress evidence exists. The current repository already has action capability fields, Task Detail entry points, task input snapshot persistence, rerun/update routes, and failed-step Resume rejection paths, but exact full rerun is only partially aligned because existing rerun submission paths can carry edited parameters or input artifact overrides. The implementation should add verification-first unit and UI coverage for the existing separated actions, then tighten exact rerun submission semantics and add integration coverage proving full retry paths do not import Resume progress.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `api_service/api/routers/executions.py` builds `canEditForRerun`, `canRerun`, and `canResumeFromFailedStep`; `frontend/src/entrypoints/task-detail.tsx` renders separate controls | Add matrix coverage for failed task details across independent action combinations; adjust UI copy or visibility if tests expose coupling | unit + integration |
| FR-002 | implemented_unverified | `frontend/src/lib/temporalTaskEditing.ts` resolves `rerunExecutionId&mode=edit`; Create page loads execution draft for rerun/edit modes | Add focused UI verification for edit-for-rerun loading from authoritative snapshot; implement fallback fixes if draft source is not canonical | unit |
| FR-003 | implemented_unverified | `frontend/src/entrypoints/task-create.tsx` permits edits in rerun edit mode and submits `RequestRerun` with a patch | Verify edit-for-rerun permits normal authoring changes and distinguish it from exact Rerun | unit |
| FR-004 | partial | Rerun route and update path exist, but `tests/unit/workflows/temporal/test_temporal_service.py` includes `test_request_rerun_can_override_inputs_and_parameters` | Change exact Rerun path so it reuses original task input unchanged; keep edited changes under Edit task only | unit + integration |
| FR-005 | implemented_unverified | Temporal service tests show manual rerun can continue as new or create a fresh execution for terminal runs | Add exact-rerun scenario asserting from-beginning path without mutation or Resume checkpoint import | unit + integration |
| FR-006 | partial | Existing rerun tests prove from-beginning reruns, but current rerun override support conflicts with exact unchanged semantics | Reject or omit edited payloads for exact Rerun; assert no preserved steps, resume checkpoint, or prior progress is imported | unit + integration |
| FR-007 | implemented_unverified | `api_service/api/routers/executions.py` persists original task input snapshots with `source_kind="rerun"`; route tests cover snapshot hydration | Add edited full retry assertion that the new execution gets its own snapshot distinct from the failed source | unit |
| FR-008 | implemented_unverified | `_build_action_capabilities()` gates actions on workflow type, feature flag, state, snapshot, and checkpoint | Add matrix tests for each action capability and disabled reason surfaced to UI | unit + integration |
| FR-009 | implemented_verified | `resume_execution_from_failed_step()` rejects edited task payload fields; `test_failed_step_resume_request_rejects_edited_task_payload_fields` covers it | Preserve behavior; include in final verification | none beyond final verify |
| FR-010 | partial | Resume requires checkpoint ref and returns `resume_not_available`, but stale/unauthorized/inconsistent evidence coverage is incomplete | Add route/service tests for missing, stale, unauthorized, and inconsistent checkpoint evidence with operator-readable reasons | unit + integration |
| FR-011 | implemented_unverified | Source execution remains terminal in service rerun tests; Resume route uses linked follow-up execution | Add explicit immutability assertions for failed source state, snapshot, step ledger refs, artifacts, and checkpoints | unit + integration |
| FR-012 | partial | Resume rejects edited payloads and capability gates exist; exact Rerun still accepts override-style payloads | Fail visibly when exact Rerun receives edited task/input mutation fields; keep Edit task as the mutation path | unit + integration |
| FR-013 | implemented_unverified | `spec.md` preserves MM-632 and the canonical Jira preset brief | Preserve traceability through plan, research, contracts, quickstart, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | implemented_unverified | Task Detail renders action controls from capability fields | Add UI matrix test for independent Edit task, Rerun, and Resume visibility | unit |
| SCN-002 | implemented_unverified | Edit-for-rerun route exists and Create page loads rerun execution draft | Add UI test proving authoritative snapshot source and editable controls | unit |
| SCN-003 | implemented_unverified | Snapshot persistence exists for rerun source kind | Add route/service test proving edited retry creates a new snapshot and imports no progress | unit + integration |
| SCN-004 | partial | Exact rerun path exists but currently can accept overrides | Add tests first, then remove override behavior from exact Rerun | unit + integration |
| SCN-005 | partial | No comprehensive no-progress-import assertion for full retry paths | Add integration evidence that full retry paths omit resume checkpoint and preserved progress fields | integration |
| SCN-006 | partial | Missing checkpoint disabled reason exists; other invalid evidence states need coverage | Add missing/stale/unauthorized/inconsistent checkpoint tests and operator-readable reason checks | unit + integration |
| SCN-007 | implemented_unverified | Some rerun source-state preservation tests exist | Add explicit source immutability assertions across all recovery action attempts | unit + integration |
| SC-001 | implemented_unverified | Existing UI and route tests cover selected happy paths | Add full matrix coverage | unit |
| SC-002 | implemented_unverified | Edit-for-rerun and snapshot hydration are present | Add end-to-end edit full retry verification | unit + integration |
| SC-003 | partial | Existing rerun can start from beginning, but unchanged input is not guaranteed | Tighten exact Rerun contract and test original input preservation | unit + integration |
| SC-004 | partial | Current rerun override support conflicts with zero progress/import mutation outcome | Add no-import and no-override assertions | unit + integration |
| SC-005 | partial | Missing checkpoint reason exists; other unavailable evidence cases incomplete | Add unavailable-evidence coverage and readable reason checks | unit + integration |
| SC-006 | implemented_unverified | Source state preservation is partly covered | Add explicit immutability tests | unit + integration |
| SC-007 | implemented_unverified | `spec.md` and this plan preserve MM-632 and source coverage IDs | Preserve through all artifacts and final verification | final verify |
| DESIGN-REQ-001 | implemented_unverified | Distinct backend capabilities and UI links exist; Resume rejects edited payloads | Add verification matrix and exact rerun fallback work if tests fail | unit + integration |
| DESIGN-REQ-002 | partial | Capability gating and Resume checkpoint checks exist; exact Rerun and invalid evidence states incomplete | Tighten exact Rerun semantics and expand invalid checkpoint evidence coverage | unit + integration |
| DESIGN-REQ-003 | implemented_unverified | Edit-for-rerun route and snapshot persistence exist | Verify edited retry snapshot creation and source immutability | unit + integration |
| DESIGN-REQ-004 | partial | Exact rerun path exists but permits override-like payloads | Remove/deny exact rerun mutation path and test original input preservation | unit + integration |
| DESIGN-REQ-005 | implemented_verified | Resume route rejects edited payload fields; full Resume execution is out of scope except action separation | Preserve behavior and final verify action separation | none beyond final verify |
| DESIGN-REQ-006 | implemented_unverified | Task input snapshot persistence and artifact-backed hydration exist | Verify edit/rerun snapshot provenance and attachment/preset preservation in focused tests | unit + integration |
| DESIGN-REQ-007 | partial | Intent separation exists, but exact Rerun override support and missing no-import coverage leave gaps | Tighten contract, add no-import tests, preserve Resume evidence requirements | unit + integration |

Status summary: 0 missing, 13 partial, 19 implemented_unverified, 2 implemented_verified.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, Vitest, pytest  
**Storage**: Existing Temporal execution records, canonical execution parameters/memo/search attributes, Temporal artifact metadata/content store, and existing original task input snapshot artifacts; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` for Python unit tests; `./tools/test_unit.sh --ui-args <path>` or `npm run ui:test -- <path>` for focused frontend iteration  
**Integration Testing**: `./tools/test_integration.sh` for hermetic integration_ci coverage; targeted integration tests under `tests/integration` should avoid external credentials  
**Target Platform**: MoonMind API service and Mission Control dashboard in the existing containerized local deployment  
**Project Type**: Web service plus frontend application with Temporal-backed orchestration  
**Performance Goals**: Failed task details continue to render action availability in the normal detail polling flow without adding additional user-visible waits; recovery submission remains a single explicit user action  
**Constraints**: Preserve in-flight Temporal compatibility for workflow/activity/update payloads; do not introduce compatibility aliases that alter semantic meaning; keep source failed execution immutable; no raw credentials or binary payloads in workflow history  
**Scale/Scope**: One runtime story for failed task recovery actions on `MoonMind.Run` task executions; excludes implementing full Resume checkpoint production beyond action separation and invalid evidence handling

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The plan keeps behavior in MoonMind's orchestration layer and does not introduce a competing agent runtime.
- **II. One-Click Agent Deployment**: PASS. No new external service, secret, or deployment prerequisite is planned.
- **III. Avoid Vendor Lock-In**: PASS. Recovery behavior is MoonMind task orchestration behavior, not provider-specific behavior.
- **IV. Own Your Data**: PASS. Recovery state uses existing MoonMind-owned records and artifacts.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill contract changes are planned.
- **VI. Scaffolds Are Replaceable, Tests Are the Anchor**: PASS. The plan starts with verification tests around recovery contracts before code changes.
- **VII. Powerful Runtime Configurability**: PASS. Existing dashboard feature flags and action capability state remain observable.
- **VIII. Modular and Extensible Architecture**: PASS. Planned changes stay within existing UI, API route, schema, and Temporal service boundaries.
- **IX. Resilient by Default**: PASS. The plan requires source immutability, explicit failure reasons, and boundary/integration coverage for recovery workflows.
- **X. Facilitate Continuous Improvement**: PASS. Verification artifacts preserve outcome and traceability for future final verification.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Planning is derived from the single-story spec and preserves all requirements.
- **XII. Documentation Separation**: PASS. Planning artifacts stay under `specs/326-expose-distinct-full-retry-recovery-actions/`; canonical docs remain desired-state source references.
- **XIII. Pre-Release Compatibility Policy**: PASS. The plan tightens internal exact-rerun semantics rather than introducing hidden compatibility aliases.

Re-check after Phase 1 design: PASS. No new constitution violations were introduced by `research.md`, `data-model.md`, `contracts/recovery-actions.md`, or `quickstart.md`.

## Project Structure

### Documentation (this feature)

```text
specs/326-expose-distinct-full-retry-recovery-actions/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── recovery-actions.md
├── checklists/
│   └── requirements.md
└── spec.md
```

### Source Code (repository root)

```text
api_service/
├── api/
│   └── routers/
│       └── executions.py
└── db/
    └── models.py

moonmind/
├── schemas/
│   └── temporal_models.py
└── workflows/
    └── temporal/
        └── service.py

frontend/
└── src/
    ├── entrypoints/
    │   ├── task-create.tsx
    │   ├── task-create.test.tsx
    │   ├── task-detail.tsx
    │   └── task-detail.test.tsx
    └── lib/
        └── temporalTaskEditing.ts

tests/
├── unit/
│   ├── api/routers/test_executions.py
│   └── workflows/temporal/test_temporal_service.py
└── integration/
    └── temporal/
```

**Structure Decision**: Use the existing API route, Temporal service, schema, and Mission Control UI layout. Add or update unit tests around API/service/UI contracts first, then add hermetic integration coverage for full recovery flows and source immutability.

## Complexity Tracking

No constitution violations require justification.
