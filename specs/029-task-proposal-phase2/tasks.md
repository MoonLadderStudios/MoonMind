# Tasks: Task Proposal Queue Phase 2

**Input**: Design documents from `/specs/025-task-proposal-phase2/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Ensure repo root `.` on branch `025-task-proposal-phase2` is rebased onto `origin/main` and dependencies installed via README scripts.
- [X] T002 Document rollout/flags context in `specs/025-task-proposal-phase2/quickstart.md` as implementation evolves.

---

## Phase 2: Foundational (Blocking Prerequisites)

- [X] T003 Create Alembic migration `api_service/migrations/versions/*_task_proposals_phase2.py` adding dedup, priority, snooze columns plus notification audit table and indexes.
- [X] T004 Update SQLAlchemy model `moonmind/workflows/task_proposals/models.py` to map new columns/enums and constraints.
- [X] T005 Extend repository + service plumbing (`moonmind/workflows/task_proposals/repositories.py` & `service.py`) to store dedup metadata during creation and include new fields in queries.
- [X] T006 Update Pydantic schemas `moonmind/schemas/task_proposal_models.py` for dedup/priority/snooze/similar structures and request payloads.
- [X] T007 Add notification plumbing scaffolding (config flags + client reuse) in `moonmind/workflows/task_proposals/service.py` ready for category-specific dispatch.

---

## Phase 3: User Story 1 – Detect Duplicates Before Promotion (Priority: P1) 🎯 MVP

Goal: Persist dedup hashes and surface similar proposals via API/UI so reviewers avoid redundant jobs.

Independent Test: Create proposals with similar titles + repo, hit `/api/proposals` and UI detail page, confirm dedup hash + similar proposals appear.

- [X] T008 [US1] Compute normalized dedup key/hash in `moonmind/workflows/task_proposals/service.py` during proposal creation/backfill existing rows.
- [X] T009 [US1] Extend repository queries to fetch similar proposals sharing dedup hash and expose via new method in `moonmind/workflows/task_proposals/repositories.py`.
- [X] T010 [US1] Update API router `api_service/api/routers/task_proposals.py` to accept `includeSimilars` and embed similar proposals/dedup fields in responses.
- [X] T011 [US1] Enhance dashboard list/detail `api_service/static/task_dashboard/dashboard.js` with dedup hash badge + "Similar Proposals" card linking to each item.
- [X] T012 [US1] Add unit tests covering dedup computation + similar fetch in `tests/unit/workflows/task_proposals/test_service.py` and API serialization in `tests/unit/api/routers/test_task_proposals.py`.

---

## Phase 4: User Story 2 – Edit Before Promote (Priority: P1)

Goal: Allow reviewers to modify the stored canonical payload before enqueuing while keeping existing one-click promote.

Independent Test: Use dashboard modal to edit instructions/priority, promote, and verify queue job uses overrides + audit note.

- [X] T013 [US2] Expand promotion service (`moonmind/workflows/task_proposals/service.py`) to accept `taskCreateRequestOverride` merged with stored envelope plus audit logging.
- [X] T014 [US2] Update request/response schemas + router handling (`moonmind/schemas/task_proposal_models.py`, `api_service/api/routers/task_proposals.py`) to validate override payload.
- [X] T015 [US2] Implement dashboard "Edit & Promote" modal with form editing instructions/git/publish/priority/attempts and call promote endpoint with overrides (`api_service/static/task_dashboard/dashboard.js`).
- [X] T016 [US2] Add backend + UI tests validating override behavior (pytest updates in `tests/unit/api/routers/test_task_proposals.py` and new frontend smoke coverage via lint-friendly JS unit or manual instrumentation comment) plus existing worker tests unaffected.

---

## Phase 5: User Story 3 – Snooze & Priority Triage (Priority: P2)

Goal: Enable reviewers to mark triage priority and temporarily hide proposals until a snooze expires.

Independent Test: Snooze a proposal via API/UI, ensure it leaves default open list until expiration, adjust priority and see badge/order update.

- [X] T017 [US3] Add snooze/unsnooze endpoints + priority update route in `api_service/api/routers/task_proposals.py` and service methods handling authorization/state transitions.
- [X] T018 [US3] Update list filtering logic to omit snoozed proposals by default while supporting `status=snoozed`/`includeSnoozed` query along with scheduler/cron hook to auto-unsnooze (likely background task or SQL job) within `moonmind/workflows/task_proposals/service.py` (include metric/telemetry).
- [X] T019 [US3] Surface snooze + priority controls/badges in dashboard list/detail (buttons, date pickers, priority chips) within `api_service/static/task_dashboard/dashboard.js`.
- [X] T020 [US3] Extend unit tests for snooze/priority endpoints plus service behavior (`tests/unit/api/routers/test_task_proposals.py`, `tests/unit/workflows/task_proposals/test_service.py`).

---

## Phase 6: User Story 4 – Notifications for Security/Tests (Priority: P2)

Goal: Send Slack/webhook alerts when new proposals in `security` or `tests` categories land, with deduplicated delivery.

Independent Test: Submit proposals in relevant categories and observe single notification event per proposal with success metric/log, while other categories remain silent.

- [X] T021 [US4] Implement notification dispatcher (Slack/webhook) invoked post-create in `moonmind/workflows/task_proposals/service.py`, persisting audit rows and emitting StatsD counters.
- [X] T022 [US4] Add configuration + secrets plumbing (e.g., `config.toml`, env var parsing, helper module) to enable/disable notifications per environment.
- [X] T023 [US4] Write unit tests stubbing notification client to ensure alerts trigger once and failures are logged without blocking (`tests/unit/workflows/task_proposals/test_service.py`).

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T024 Update `docs/TaskProposalQueue` to mark Phase 2 complete, describe new endpoints/UI workflows, and capture notification enablement steps.
- [X] T025 Run full unit suite via `./tools/test_unit.sh` and fix regressions.
- [X] T026 Perform manual dashboard smoke test (list/detail/promote/edit/snooze) and record notes in `specs/025-task-proposal-phase2/quickstart.md`.

---

## Dependencies & Execution Order

1. Complete Phase 1 setup to ensure branch + docs ready.
2. Phase 2 foundational tasks must finish before user stories.
3. User Story 1 (dedup) unlocks user-facing trust signals and should be implemented first (P1). User Story 2 also P1 and can run after service/schema groundwork lands (T003–T007).
4. User Story 3 depends on migration + API scaffolding but can proceed in parallel with US2 once service/responses updated.
5. User Story 4 depends on creation service hooking (reuse dedup fields) and should run after US1 to ensure dedup data is available for metrics.
6. Polish tasks finalize docs/tests.

Parallel opportunities: tasks marked [P] can run concurrently (none explicitly flagged beyond general concurrency). US2 and US3 can proceed in parallel after foundational updates if staffed.
