# Tasks: Temporal Artifact Presentation Contract

**Input**: Design documents from `/specs/047-temporal-artifact-presentation/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Tests are required because the feature specification mandates production validation coverage (`FR-002`).  
**Organization**: Tasks are grouped by user story to preserve independent implementation and validation.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes concrete file paths or validation commands

## Runtime Scope Controls

- Runtime implementation tasks are explicitly represented in `T001-T006`, `T009-T012`, `T015-T018`, and `T020-T022`.
- Runtime validation tasks are explicitly represented in `T007-T008`, `T013-T014`, `T019`, and `T024-T026`, with `DOC-REQ-*` traceability validation enforced in `T024-T025`.
- `DOC-REQ-001` through `DOC-REQ-010` implementation + validation coverage is enforced by the per-task tags and the `DOC-REQ Coverage Matrix` in this file, with persistent requirement mapping in `specs/047-temporal-artifact-presentation/contracts/requirements-traceability.md`.
- This feature is runtime implementation mode only; docs-only completion is invalid.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the shared route, runtime-config, and artifact-policy surfaces that every story depends on.

- [X] T001 [P] Expose Temporal detail, action, submit, and artifact endpoint templates plus feature-flag defaults in `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-010).
- [X] T002 [P] Harden canonical `/tasks/:taskId` route acceptance and normalization for `mm:` workflow IDs in `api_service/api/routers/task_dashboard.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-010).
- [X] T003 [P] Extend Temporal artifact read-policy response fields for preview/default-read/raw-access metadata in `api_service/api/routers/temporal_artifacts.py` and `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-009).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build dashboard helpers that must exist before any story can render or validate Temporal detail correctly.

**⚠️ CRITICAL**: No user story work should begin until this phase is complete.

- [X] T004 Implement latest-run scope resolution and empty latest-run fallback helpers in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-008).
- [X] T005 Implement reusable Temporal artifact presentation/access helper exports for the dashboard runtime test harness in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-009, DOC-REQ-010).
- [X] T006 [P] Normalize task-oriented Temporal summary, status, waiting-context, and timeline shaping in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-002, DOC-REQ-003).

**Checkpoint**: Route/config/runtime helpers are ready and story work can proceed.

---

## Phase 3: User Story 1 - View the Right Artifacts for a Temporal Task (Priority: P1) 🎯 MVP

**Goal**: Render a task-oriented Temporal detail page that resolves by `taskId`, fetches execution detail first, and shows only latest-run artifacts.

**Independent Test**: Open a Temporal-backed task detail page for an execution with multiple runs and linked artifacts, then verify that the page resolves by `taskId`, loads execution detail first, and displays only artifacts from the latest run.

### Tests for User Story 1

- [X] T007 [P] [US1] Add Node dashboard runtime tests for detail-first fetch ordering, latest-run artifact scoping, and no-mixed-run fallback behavior in `tests/task_dashboard/test_temporal_detail_runtime.js` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-008, DOC-REQ-010).
- [X] T008 [P] [US1] Add Python route/runtime-config tests for canonical Temporal task IDs and detail/debug endpoint exposure in `tests/unit/api/routers/test_task_dashboard.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-010).

### Implementation for User Story 1

- [X] T009 [US1] Implement Temporal detail loading that fetches `/api/executions/{workflowId}` before latest-run artifacts in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-008).
- [X] T010 [US1] Render task-oriented Temporal header fields with advanced execution metadata kept secondary in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-002).
- [X] T011 [US1] Render synthesized summary, waiting-context, and timeline sections without raw-history-first output in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-003).
- [X] T012 [US1] Preserve canonical `taskId == workflowId` detail routing across reruns and Continue-As-New flows in `api_service/api/routers/task_dashboard.py` and `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-001, DOC-REQ-005).

**Checkpoint**: US1 delivers the MVP Temporal detail experience.

---

## Phase 4: User Story 2 - Safely Preview and Download Execution Artifacts (Priority: P1)

**Goal**: Present execution-linked artifacts with preview-first, policy-aware access while keeping large inputs/outputs artifact-first.

**Independent Test**: Create or link artifacts with preview and raw-access variants, then verify preview-first rendering, restricted raw access handling, and download behavior through authorized artifact endpoints.

### Tests for User Story 2

- [X] T013 [P] [US2] Add Node runtime tests for preview-first actions, restricted raw access, no-safe-preview notes, and latest-run artifact labels in `tests/task_dashboard/test_temporal_detail_runtime.js` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-009, DOC-REQ-010).
- [X] T014 [P] [US2] Add Python router/runtime-config tests for artifact metadata, presign-download, and authorized download endpoint wiring in `tests/unit/api/routers/test_temporal_artifacts.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-006, DOC-REQ-009, DOC-REQ-010).

### Implementation for User Story 2

- [X] T015 [US2] Implement artifact presentation normalization from link metadata, preview refs, default read refs, and raw-access policy in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-007, DOC-REQ-009).
- [X] T016 [US2] Implement preview-first and raw-download artifact actions through MoonMind-controlled access URLs in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-006, DOC-REQ-009).
- [X] T017 [US2] Wire artifact-first create, upload, complete, metadata, and download helpers for Temporal task flows in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-006).
- [X] T018 [US2] Route Temporal task artifact edits to create new artifact references instead of mutating existing artifacts in `api_service/static/task_dashboard/dashboard.js` and `moonmind/schemas/temporal_artifact_models.py` (DOC-REQ-007).

**Checkpoint**: US2 delivers safe artifact access and artifact-first task flows.

---

## Phase 5: User Story 3 - Use Task-Oriented Controls Without Temporal Jargon (Priority: P2)

**Goal**: Keep Temporal-backed actions and submit behavior task-oriented while mapping them onto execution operations behind the scenes.

**Independent Test**: Exercise enabled controls on a Temporal-backed task and verify that action availability follows the documented state matrix while labels remain task-oriented.

### Tests for User Story 3

- [X] T019 [P] [US3] Add dashboard action-surface tests for state-gated controls, task-oriented labels, stable rerun routing, and hidden Temporal runtime selection in `tests/task_dashboard/test_temporal_detail_runtime.js` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-010).

### Implementation for User Story 3

- [X] T020 [US3] Expose config-gated Temporal action and submit capabilities in `api_service/api/routers/task_dashboard_view_model.py` and `api_service/api/routers/executions.py` (DOC-REQ-004, DOC-REQ-005).
- [X] T021 [US3] Implement task-oriented Temporal control rendering and action-to-update/signal/cancel mapping in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-004).
- [X] T022 [US3] Keep Temporal submit flows task-oriented and prevent a visible Temporal runtime selector in `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-005).

**Checkpoint**: US3 delivers task-oriented control and submit behavior without leaking Temporal jargon.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability, run the repository-standard validation path, and enforce runtime gates.

- [X] T023 [P] Sync final route, run-scope, artifact presentation, and action-posture details in `specs/047-temporal-artifact-presentation/contracts/temporal-artifact-presentation-contract.md` and `specs/047-temporal-artifact-presentation/contracts/requirements-traceability.md` (DOC-REQ-001 through DOC-REQ-009).
- [X] T024 Run focused Temporal dashboard/router/artifact regression via `./tools/test_unit.sh` covering `tests/task_dashboard/test_temporal_detail_runtime.js`, `tests/unit/api/routers/test_task_dashboard.py`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, `tests/unit/api/routers/test_temporal_artifacts.py`, and `tests/unit/specs/test_doc_req_traceability.py` (DOC-REQ-001 through DOC-REQ-010 validation sweep).
- [X] T025 Run full repository unit validation via `./tools/test_unit.sh` and resolve remaining Temporal dashboard regressions in `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/task_dashboard_view_model.py`, `api_service/api/routers/temporal_artifacts.py`, `api_service/static/task_dashboard/dashboard.js`, `moonmind/schemas/temporal_artifact_models.py`, and `tests/` (DOC-REQ-010).
- [X] T026 Execute runtime scope gates with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` (DOC-REQ-010).
- [X] T027 [P] Record final runtime verification steps and expected Temporal detail behavior in `specs/047-temporal-artifact-presentation/quickstart.md` (DOC-REQ-010).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user-story work.
- **Phase 3 (US1)**: Depends on Phase 2 completion.
- **Phase 4 (US2)**: Depends on Phase 2 completion and can proceed independently from US1 once shared helpers land.
- **Phase 5 (US3)**: Depends on Phase 2 completion and benefits from US1 detail rendering being in place.
- **Phase 6 (Polish)**: Depends on completion of targeted story phases.

### User Story Dependencies

- **US1 (P1)**: Primary MVP and first delivery slice after foundational work.
- **US2 (P1)**: Independent artifact-access slice after foundational work; integrates cleanly with US1 but is separately testable.
- **US3 (P2)**: Depends on shared Temporal detail/config surfaces and finalizes action/submit behavior.

### Within Each User Story

- Add or update tests for the story first, verify they fail, then implement.
- Complete helper/model/config shaping before wiring UI interactions.
- Re-run story-specific tests before moving to the next story.

### Parallel Opportunities

- Setup tasks `T001-T003` can run in parallel.
- Foundational task `T006` can run in parallel once `T004-T005` scope is clear.
- US1 test tasks `T007-T008` can run in parallel.
- US2 test tasks `T013-T014` can run in parallel.
- Polish doc/verification tasks `T023` and `T027` can run in parallel after runtime behavior stabilizes.

---

## Parallel Example: User Story 1

```bash
# Execute US1 validation tracks concurrently:
Task T007: tests/task_dashboard/test_temporal_detail_runtime.js
Task T008: tests/unit/api/routers/test_task_dashboard.py + tests/unit/api/routers/test_task_dashboard_view_model.py
```

## Parallel Example: User Story 2

```bash
# Execute US2 validation tracks concurrently:
Task T013: tests/task_dashboard/test_temporal_detail_runtime.js
Task T014: tests/unit/api/routers/test_temporal_artifacts.py + tests/unit/api/routers/test_task_dashboard_view_model.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2 foundations.
2. Deliver Phase 3 (US1) canonical detail routing and latest-run artifact loading.
3. Validate US1 independently with `T007-T008`.
4. Demo or review the MVP Temporal detail experience.

### Incremental Delivery

1. Foundation (Phases 1-2) establishes route/config/helper behavior.
2. Add US1 for latest-run Temporal detail rendering.
3. Add US2 for preview-first artifact access and artifact-first flows.
4. Add US3 for task-oriented controls and submit posture.
5. Finish with Phase 6 validation and scope gates.

### Parallel Team Strategy

1. Collaborate on Phase 1-2 foundations.
2. Split by story once foundations are stable:
   - Engineer A: US1 detail route and latest-run rendering
   - Engineer B: US2 artifact presentation and access flows
   - Engineer C: US3 action/submit posture and config gates
3. Rejoin for cross-cutting validation and traceability updates.

---

## Quality Gates

1. Runtime tasks gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime diff gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
3. Unit/runtime gate: `./tools/test_unit.sh`
4. Traceability gate: each `DOC-REQ-*` keeps at least one implementation task and one validation task.
5. Runtime-mode gate: production runtime code and automated validation must both be present before completion.

## Task Summary

- Total tasks: **27**
- Story task count: **US1 = 6**, **US2 = 6**, **US3 = 4**
- Parallelizable tasks (`[P]`): **11**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit paths or validation commands**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T002, T004, T009, T012 | T007, T008 |
| DOC-REQ-002 | T006, T010 | T008 |
| DOC-REQ-003 | T006, T011 | T007 |
| DOC-REQ-004 | T001, T020, T021 | T019 |
| DOC-REQ-005 | T001, T002, T012, T020, T022 | T008, T019 |
| DOC-REQ-006 | T001, T003, T005, T016, T017 | T013, T014, T024 |
| DOC-REQ-007 | T003, T005, T015, T018 | T013, T014, T024 |
| DOC-REQ-008 | T004, T009 | T007 |
| DOC-REQ-009 | T003, T005, T015, T016 | T013, T014, T024 |
| DOC-REQ-010 | T001, T002, T003, T005 | T007, T008, T013, T014, T019, T024, T025, T026 |

Coverage rule: do not close implementation until every `DOC-REQ-*` row keeps both implementation and validation coverage.
