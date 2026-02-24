# Tasks: Submit Runtime Selector

**Input**: Design docs from `/specs/038-submit-runtime-selector/`  
**Prerequisites**: `plan.md`, `spec.md`, + supporting artifacts (`data-model.md`, `contracts/submit-work-form.md`, `research.md`, `quickstart.md`)  
**Tests**: Execute JavaScript + Python suites through `./tools/test_unit.sh` (per Testing Instructions).  
**Organization**: Tasks are grouped by user story so each slice stays independently testable.

## Phase 1: Setup (Shared Infrastructure)

Purpose: Confirm existing dashboard config + route entry points before refactoring.

- [X] T001 Capture the current runtime + endpoint config surface area by reviewing `api_service/api/routers/task_dashboard_view_model.py` and the config bootstrap in `api_service/static/task_dashboard/dashboard.js` so the selector pulls from the right `system.supportedTaskRuntimes` data.
- [X] T002 Map how `/tasks/queue/new` and `/tasks/orchestrator/new` currently mount separate submit pages inside `api_service/static/task_dashboard/dashboard.js` and `api_service/api/routers/task_dashboard.py` to identify the hooks that must share a single renderer.

---

## Phase 2: Foundational (Blocking Prerequisites)

Purpose: Stand up the unified form shell + runtime option plumbing that every story depends on.

- [X] T003 Replace the legacy queue/orchestrator submit DOM builders with a new `renderSubmitWorkPage(presetRuntime)` inside `api_service/static/task_dashboard/dashboard.js`, then ensure both route handlers call this shared entry.
- [X] T004 Build the runtime selector scaffold in `api_service/static/task_dashboard/dashboard.js` by deriving options from config, appending the UI-only `orchestrator` value, rendering the shared instruction textarea, and establishing worker vs. orchestrator section containers.

**Checkpoint**: Shared `SubmitWorkForm` renders in both routes with a 4-way selector stub.

---

## Phase 3: User Story 1 – Worker Task Submission (Priority: P1) 🎯 MVP

**Goal**: Worker runtimes keep the full queue-task form (steps, presets, repo, publish, priority/maxAttempts) with no orchestrator noise.  
**Independent Test**: From `/tasks/queue/new`, select each worker runtime, submit a valid queue payload, and confirm redirects go to `/tasks/queue/:jobId`.

- [X] T005 [US1] Embed the existing queue submit controls (step editor, presets, repo/branch, publish, priority/maxAttempts, propose tasks) inside the worker section of `api_service/static/task_dashboard/dashboard.js`, ensuring they stay visible only when `runtime !== "orchestrator"`.
- [X] T006 [P] [US1] Reuse the current queue validation + payload serialization logic inside `api_service/static/task_dashboard/dashboard.js`, but route worker submissions through `determineSubmitDestination(...).endpoint` so `/api/queue/jobs` receives the existing `{ type: "task" }` body.
- [X] T007 [US1] Keep runtime defaults (model, effort, publish mode, repository fallback) in sync per selected worker runtime by wiring `resolveRuntimeDefault` + step editor mirroring in `api_service/static/task_dashboard/dashboard.js`.
- [X] T008 [P] [US1] Extend `tests/task_dashboard/test_submit_runtime.js` with worker-focused assertions (e.g., queue destination + validation errors) using the exposed helpers.

**Checkpoint**: Worker flows unchanged aside from the new runtime selector UX.

---

## Phase 4: User Story 2 – Orchestrator Run Submission (Priority: P2)

**Goal**: Selecting Orchestrator hides queue-only fields and submits `{ instruction, targetService, priority, approvalToken? }` to `/orchestrator/runs`.  
**Independent Test**: From `/tasks/orchestrator/new`, fill the orchestrator fields, submit, and land on `/tasks/orchestrator/:runId`.

- [X] T009 [US2] Add the orchestrator-only field group (target service, enum priority, optional approval token) plus button label changes inside `api_service/static/task_dashboard/dashboard.js`, ensuring worker controls stay hidden in this mode.
- [X] T010 [US2] Implement `validateOrchestratorSubmission` in `api_service/static/task_dashboard/dashboard.js` to enforce `instruction` + `targetService`, normalize `priority` to `normal|high`, trim `approvalToken`, and surface inline errors.
- [X] T011 [P] [US2] In the form submit handler inside `api_service/static/task_dashboard/dashboard.js`, branch on `runtime === "orchestrator"` to POST to `sources.orchestrator.create` and redirect to `/tasks/orchestrator/{runId}`, keeping drafts intact on failure.
- [X] T012 [P] [US2] Expand `tests/task_dashboard/test_submit_runtime.js` with orchestrator validations (missing fields, priority normalization, endpoint routing) using the new helper exports.

**Checkpoint**: Orchestrator runs can be launched from the shared page without touching queue validation.

---

## Phase 5: User Story 3 – Runtime Switching Without Draft Loss (Priority: P3)

**Goal**: Worker + orchestrator drafts persist independently while toggling runtimes, including instruction text.  
**Independent Test**: Populate worker fields, switch to orchestrator and add its data, then toggle ≥5 times and confirm each runtime restores its previous entries.

- [X] T013 [US3] Implement `createSubmitDraftController` plus helper cloning utilities in `api_service/static/task_dashboard/dashboard.js` so worker/orchestrator drafts store independent copies of form state (steps, presets, fields).
- [X] T014 [P] [US3] Wire the runtime select change handler in `api_service/static/task_dashboard/dashboard.js` to `save` the outgoing draft, apply defaults for the new runtime, and `load` the incoming draft, including mirroring the shared instruction field + primary step.
- [X] T015 [US3] Expose `createSubmitDraftController`, `determineSubmitDestination`, and `validateOrchestratorSubmission` under `window.__submitRuntimeTest` plus ensure `syncRuntimeSections` updates button labels + visibility in `api_service/static/task_dashboard/dashboard.js`.
- [X] T016 [P] [US3] Add unit coverage in `tests/task_dashboard/test_submit_runtime.js` for draft isolation, helper exports, and runtime toggling (including mutation safety).

**Checkpoint**: Runtime toggling feels lossless within a single-page session.

---

## Phase 6: Polish & Cross-Cutting Concerns

Purpose: Rebuild assets, capture QA evidence, and run the mandated suites.

- [ ] T017 [P] Rebuild the dashboard JavaScript bundle via `npm run dashboard:js` (package root) so `api_service/static/task_dashboard/dashboard.js` ships minified changes.
- [X] T018 Document the runtime toggle + endpoint QA results in the validation table inside `specs/038-submit-runtime-selector/quickstart.md`.
- [X] T019 Run the full validation suite with `./tools/test_unit.sh` from repo root and address any regressions before handoff.
- [X] T020 Execute `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` to satisfy the runtime gate after code + docs stabilize.

---

## Dependencies & Execution Order

- Setup (Phase 1) has no prerequisites; complete before touching UI code.
- Foundational work (Phase 2) depends on Setup and **blocks all user stories**.
- User Story phases (3–5) all depend on Phase 2 completion. Implement in priority order (US1 → US2 → US3) to satisfy MVP + incremental rollout, but US2/US3 can run in parallel once their prerequisites are met.
- Polish (Phase 6) depends on all targeted user stories being code-complete.

## User Story Dependencies

- **US1**: First deliverable after Foundation; establishes the MVP worker path.
- **US2**: Depends on shared form shell + worker validations staying isolated, but not on US3.
- **US3**: Depends on shared form shell + both runtime sections existing so drafts have data to store.

## Parallel Opportunities

- Tasks marked `[P]` touch disjoint areas: e.g., T006 worker payload routing, T010 orchestrator validation, T014 runtime toggles, T017 build/test prep can run concurrently once their dependencies clear.
- Different engineers can split by user story after Phase 2 (one on worker polish + tests, one on orchestrator path, one on draft persistence).

## Implementation Strategy

1. Complete Phases 1–2 to land the shared form skeleton plus selector.
2. Ship **MVP = User Story 1** (worker submissions) by finishing T005–T008, then run `./tools/test_unit.sh`.
3. Layer in User Story 2 to unlock orchestrator submissions while keeping tests green.
4. Add User Story 3 draft persistence for ergonomics.
5. Finish with Phase 6 to rebuild assets, document QA, and re-run tests before handoff.
