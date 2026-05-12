# Implementation Plan: Editable Full Retry Workflow

**Branch**: `change-jira-issue-mm-644-to-status-in-pr-2d46bf18` | **Date**: 2026-05-12 | **Spec**: [spec.md](spec.md)
**Input**: Single-story feature specification from `specs/343-editable-full-retry-workflow/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed job branch name is not in the script's expected `NNN-feature-name` form. Planning proceeds from the active feature directory `specs/343-editable-full-retry-workflow` recorded in `.specify/feature.json`.

## Summary

MM-644 requires Edit task on a failed execution to open edit-for-rerun mode from the authoritative task input snapshot, allow normal authoring edits, create a new from-beginning execution with its own snapshot, keep the source execution immutable, import no completed progress, and preserve edited-full-retry provenance. The repository already has partial support: Task Detail can expose `canEditForRerun`, the Create page parses `rerunExecutionId&mode=edit`, snapshot-based draft hydration exists, `RequestRerun` can create a fresh terminal execution, and full-rerun sanitization strips Resume progress fields. Planning identifies verification-first work plus targeted implementation gaps around snapshot readability eligibility, edited-full-retry provenance, explicit edit-for-rerun UI/API tests, and proof that a changed edited retry creates a new snapshot without mutating source evidence.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | `_build_action_capabilities()` requires a snapshot ref before `canEditForRerun`, but does not prove artifact readability for the current user | Verify or tighten eligibility so Edit task is offered only with readable authoritative snapshot evidence | unit + integration |
| FR-002 | implemented_unverified | `taskEditForRerunHref()` and `resolveTaskSubmitPageMode()` map `rerunExecutionId&mode=edit`; Create page reads authoritative snapshot artifacts | Add UI test for edit-for-rerun route loading from snapshot; implementation contingency if hydration differs from exact rerun | UI unit |
| FR-003 | implemented_unverified | Create page edit/rerun form supports editing task instructions, steps, attachments, runtime, publish, branch, presets, and dependencies through shared authoring state | Add focused edit-for-rerun UI tests for representative authoring edits | UI unit |
| FR-004 | implemented_unverified | Edited retry uses normal submit validation and `buildEditParametersPatch()` before `RequestRerun` | Add tests proving invalid edited authoring state blocks submission before update call | UI unit |
| FR-005 | implemented_unverified | Terminal `RequestRerun` path creates a fresh execution through `_create_fresh_rerun_execution()`; Task Detail exposes edit only for terminal states | Add integration test for edited full retry creating a distinct execution and leaving source terminal record in place | integration |
| FR-006 | partial | `_persist_original_task_input_snapshot_from_parameters()` persists snapshots after task editing updates, with source kind `rerun`; no direct test proves edited retry receives a new snapshot | Add integration test for edited full retry snapshot creation and metadata/source lineage; implement fixes if snapshot is missing or attached to source | integration |
| FR-007 | implemented_unverified | `_full_rerun_parameters()` strips taskRunId/dependency and Resume carryover before rerun; new execution starts with fresh counters | Add test proving edited full retry starts with no imported step/run progress | integration |
| FR-008 | implemented_unverified | Fresh terminal rerun creates a new record; existing full-rerun tests prove source record remains terminal for exact rerun | Add edited-full-retry source immutability test covering snapshot, ledger/progress refs, artifacts, and checkpoint refs | integration |
| FR-009 | implemented_unverified | `_strip_resume_reference_parameters()` removes `resumeSource`, checkpoint refs, preserved/completed steps, and task `recovery`/`resume` from full rerun params | Add edited full retry regression test with Resume-shaped source metadata and changed payload | unit + integration |
| FR-010 | partial | `TaskRecoveryProvenance` supports `edited_full_retry`, but current RequestRerun path mainly records top-level `rerunSource` and snapshot source metadata | Add or derive explicit edited-full-retry provenance at the accepted boundary; test exact rerun vs edited retry distinction | unit + integration |
| FR-011 | partial | Missing snapshot disables edit/rerun capabilities; unreadable/corrupt snapshot load errors are handled by the Create page, but action availability may not expose that reason | Add unavailable/read-failure reason tests and implementation fallback for operator-readable ineligibility | unit + UI |
| FR-012 | implemented_unverified | `spec.md` preserves MM-644 and original preset brief | Preserve MM-644 in plan, research, data model, contract, quickstart, tasks, implementation notes, verification, commit, and PR metadata | final verify |
| SCN-001 | implemented_unverified | Capability and route plumbing exist | Add end-to-end UI test for failed execution Edit task -> edit-for-rerun route -> snapshot-hydrated form | UI unit |
| SCN-002 | implemented_unverified | Shared Create page state supports edits and validation | Add edit-for-rerun tests for representative supported fields and validation failure | UI unit |
| SCN-003 | partial | Service fresh rerun path exists; edited vs exact intent is not explicit enough in provenance | Add integration test for changed edited retry, provenance, and new execution identity | integration |
| SCN-004 | partial | Snapshot persistence exists after update; no direct edited retry proof | Add artifact-backed snapshot assertion for new execution | integration |
| SCN-005 | implemented_unverified | Exact rerun immutability is tested; edited retry source immutability needs specific coverage | Add source record/artifact/checkpoint immutability assertions | integration |
| SCN-006 | implemented_unverified | Full rerun sanitization removes progress references | Add edited retry no-progress import regression | unit + integration |
| SCN-007 | partial | Missing snapshot has disabled reason; unreadable/insufficient snapshot behavior needs stronger user-visible coverage | Add UI/API tests for missing, unreadable, unauthorized, and insufficient snapshots | unit + UI |
| SC-001 | implemented_unverified | UI can open rerun/edit modes from task detail links | Add 100% eligible-case route and hydration test | UI unit |
| SC-002 | partial | Fresh rerun and snapshot persistence exist separately | Add combined valid edited retry test proving new execution + new snapshot + source unchanged | integration |
| SC-003 | implemented_unverified | Full rerun sanitization strips completed progress fields | Add edited-full-retry coverage for every progress/checkpoint carryover field | unit + integration |
| SC-004 | partial | Missing snapshot disables actions; unreadable/insufficient snapshot needs bounded reason | Add ineligible-case matrix and implementation fixes as needed | unit + UI |
| SC-005 | implemented_unverified | `spec.md`, this plan, and design artifacts preserve MM-644 | Preserve traceability through downstream artifacts and final verification | final verify |
| DESIGN-REQ-001 | implemented_unverified | Edit-for-rerun route and authoritative snapshot hydration exist | Add route/hydration verification | UI unit |
| DESIGN-REQ-002 | implemented_unverified | Create page shared form permits edits under normal validation | Add representative edit and validation tests | UI unit |
| DESIGN-REQ-003 | implemented_unverified | RequestRerun creates fresh terminal execution or continue-as-new for active records | Add terminal edited retry from-beginning execution test | integration |
| DESIGN-REQ-004 | partial | Snapshot persistence exists but lacks edited retry proof | Add/repair new snapshot persistence for edited full retry | integration |
| DESIGN-REQ-005 | implemented_unverified | Fresh rerun source immutability exists for exact rerun | Add edited retry-specific immutability verification | integration |
| DESIGN-REQ-006 | implemented_unverified | Full rerun sanitization removes Resume progress metadata | Add edited retry no-progress import tests | unit + integration |

Status summary: 9 partial, 21 implemented_unverified, 0 missing, 0 implemented_verified.

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React for Mission Control UI behavior  
**Primary Dependencies**: FastAPI, SQLAlchemy async ORM, Pydantic v2, Temporal Python SDK, React, TanStack Query, Zod, Vitest, pytest  
**Storage**: Existing Temporal execution records, memo/search attributes, Temporal artifact metadata/content store, authoritative task input snapshot artifacts; no new persistent database table planned  
**Unit Testing**: `./tools/test_unit.sh`; focused Python tests in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/tasks/test_task_contract.py`, and focused UI tests through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx`  
**Integration Testing**: `./tools/test_integration.sh`; add hermetic `integration_ci` coverage in `tests/integration/temporal/` for service/API boundary behavior around edited full retry creation, snapshot persistence, source immutability, and progress stripping  
**Target Platform**: MoonMind API service, Temporal execution service, artifact store, and Mission Control task detail/create UI in the existing containerized deployment  
**Project Type**: Web service plus frontend dashboard with Temporal-backed orchestration  
**Performance Goals**: Task detail action capability checks remain bounded for dashboard polling; edit-for-rerun draft hydration performs one authoritative snapshot read when opening the authoring page; edited retry submission fails before creating new work when snapshot/provenance requirements are invalid  
**Constraints**: Preserve in-flight Temporal invocation compatibility or document an explicit cutover; do not add hidden fallback behavior; do not mutate source execution evidence; keep large task input content in artifacts; preserve MM-644 traceability  
**Scale/Scope**: One runtime story for `MoonMind.Run` editable full retry from failed executions; excludes exact full rerun and failed-step Resume behavior except where needed to keep edited full retry distinct

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The work strengthens MoonMind recovery orchestration around existing agent workflows.
- **II. One-Click Agent Deployment**: PASS. No new external service, secret, or deployment prerequisite is planned.
- **III. Avoid Vendor Lock-In**: PASS. Recovery state is MoonMind-owned task and artifact metadata, not provider-specific behavior.
- **IV. Own Your Data**: PASS. Authoritative snapshots and retry evidence remain in operator-controlled artifact/execution stores.
- **V. Skills Are First-Class and Easy to Add**: PASS. No agent skill runtime changes are planned.
- **VI. Scaffolds Are Replaceable, Tests Are the Anchor**: PASS. The plan requires verification-first tests around recovery contracts and UI/API behavior.
- **VII. Powerful Runtime Configurability**: PASS. Existing task-editing feature flags and action capability surfaces remain observable.
- **VIII. Modular and Extensible Architecture**: PASS. Planned work stays inside existing API, Temporal service, task contract, artifact, and UI boundaries.
- **IX. Resilient by Default**: PASS. The story improves deterministic recovery and requires explicit failure reasons before new work starts.
- **X. Facilitate Continuous Improvement**: PASS. Artifacts preserve traceability and evidence for final verification.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Planning derives from the single-story spec and keeps all requirements visible.
- **XII. Documentation Separation**: PASS. Planning and rollout details stay under `specs/343-editable-full-retry-workflow/`.
- **XIII. Pre-Release Compatibility Policy**: PASS. Any internal contract tightening should remove stale shapes in the same change unless Temporal payload compatibility requires a documented cutover.

Re-check after Phase 1 design: PASS. `research.md`, `data-model.md`, `contracts/editable-full-retry.md`, and `quickstart.md` introduce no constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/343-editable-full-retry-workflow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── editable-full-retry.md
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
│   └── workflows/tasks/test_task_contract.py
└── integration/
    └── temporal/
        ├── test_full_retry_recovery_actions.py
        └── test_backend_resume_eligibility.py
```

**Structure Decision**: Use the existing execution detail action capability route, Create page edit/rerun mode, Temporal execution update route, authoritative snapshot artifact helper, task recovery contract models, and Temporal execution service rerun behavior. Add verification-first tests at UI, API, contract, and hermetic integration boundaries, then implement only the gaps exposed by those tests.

## Complexity Tracking

No constitution violations require justification.
