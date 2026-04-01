# Tasks: Live Log Tailing

**Input**: Design documents from `/specs/084-live-log-tailing/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No new project structure needed; verify existing infrastructure is ready.

- [x] T001 Verify `web_ro` field is returned by `GET /api/queue/jobs/{id}/live-session` and `GET /api/task-runs/{id}/live-session` in local dev environment

---

## Phase 2: Foundational (Feature Flag)

**Purpose**: Add the `logStreamingEnabled` feature flag so the panel can be controlled at runtime.

- [x] T002 Add `MOONMIND_LOG_STREAMING_ENABLED` environment variable (default `true`) and expose `logStreamingEnabled` in the view model features config in `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-010, FR-011)
- [x] T003 Add unit test for `logStreamingEnabled` flag in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-010, FR-011)

**Checkpoint**: Feature flag wired and tested.

---

## Phase 3: User Story 1 — Operator Views Live Agent Output (Priority: P1) 🎯 MVP

**Goal**: Operators can toggle open a Live Output panel on the task detail page and see live terminal output from the running agent.

**Independent Test**: Start a managed agent task, navigate to its detail page, toggle the panel, and verify terminal output appears via the tmate web RO viewer.

### Implementation for User Story 1

- [x] T004 [US1] Add `renderLiveOutputPanel(state)` function to `api_service/static/task_dashboard/dashboard.js` that renders a collapsible panel with an iframe embedding `web_ro` URL when session status is `ready` (DOC-REQ-001, DOC-REQ-004, FR-001, FR-002, FR-013)
- [x] T005 [US1] Wire the Live Output panel into the queue detail view HTML in `api_service/static/task_dashboard/dashboard.js` — place it below the header and above the existing live session card section (DOC-REQ-001, FR-001)
- [x] T006 [US1] Implement panel collapse/expand toggle: on expand create iframe with `web_ro` src; on collapse remove iframe to terminate connection (DOC-REQ-003, DOC-REQ-005, FR-003, FR-008)
- [x] T007 [US1] Gate the Live Output panel rendering behind the `logStreamingEnabled` feature flag from the view model config (DOC-REQ-010, FR-011)

**Checkpoint**: Live Output panel shows live terminal output for READY sessions. Panel toggle works. Feature flag controls visibility.

---

## Phase 4: User Story 2 — Correct State Feedback (Priority: P1)

**Goal**: The panel displays appropriate messages for every session lifecycle state.

**Independent Test**: Navigate to tasks in each lifecycle state and verify the panel shows the correct message.

### Implementation for User Story 2

- [x] T008 [US2] Add state-driven rendering logic in `renderLiveOutputPanel()`: show loading indicator for STARTING, "Session ended" for ENDED/REVOKED, "Live output is not available for this task" for DISABLED/ERROR (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-011, FR-004, FR-005, FR-006, FR-012)
- [x] T009 [US2] Handle Temporal-sourced tasks (task_runs router) in addition to queue-sourced tasks — use the `web_ro` from `GET /api/task-runs/{id}/live-session` when the detail source is Temporal (DOC-REQ-009, FR-007)

**Checkpoint**: Panel correctly shows all 6 lifecycle states.

---

## Phase 5: User Story 3 — Background Tab Behavior (Priority: P2)

**Goal**: Stream pauses when the tab is backgrounded and resumes when the tab regains focus.

**Independent Test**: Open panel, switch to another tab, verify stream stops, switch back, verify stream resumes.

### Implementation for User Story 3

- [x] T010 [US3] Add `visibilitychange` event listener in `api_service/static/task_dashboard/dashboard.js` that removes the iframe when the tab is hidden and recreates it when the tab becomes visible (if the panel is open and session is READY) (DOC-REQ-003, DOC-REQ-012, FR-009, FR-010)

**Checkpoint**: Stream disconnects on tab hide, reconnects on tab show.

---

## Phase 6: User Story 4 — Feature Flag Control (Priority: P2)

**Goal**: Platform operator can disable the panel via feature flag.

**Independent Test**: Toggle the flag and verify panel appears/disappears.

This story is covered by T002, T003, and T007 (already in earlier phases). No additional tasks needed.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Validation, documentation, and cleanup.

- [x] T011 Add CSS styles for the Live Output panel (collapsible header, iframe container, loading spinner, status messages) in `api_service/static/task_dashboard/dashboard.js` inline styles or in `api_service/static/task_dashboard/` CSS
- [x] T012 Run unit tests via `./tools/test_unit.sh` and verify no regressions
- [x] T013 Run quickstart.md validation: start docker compose stack, create a managed agent task, verify Live Output panel renders correctly in all states

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — verify existing infra
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS user stories (feature flag must exist first)
- **User Story 1 (Phase 3)**: Depends on Phase 2 — core panel implementation
- **User Story 2 (Phase 4)**: Depends on Phase 3 — adds state-aware rendering to the panel
- **User Story 3 (Phase 5)**: Depends on Phase 3 — adds visibility behavior
- **Polish (Phase 7)**: Depends on Phases 3-5

### Within Each User Story

- Core rendering before state handling
- State handling before visibility behavior

### Parallel Opportunities

- T002 and T003 can run in parallel (different files)
- T004, T005, T006, T007 are sequential within the same file
- T010 (visibility listener) can start after T004 is complete, in parallel with T008/T009
- T011 (CSS) can run in parallel with T010

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Verify existing infrastructure
2. Complete Phase 2: Feature flag
3. Complete Phase 3: Core Live Output panel
4. **STOP and VALIDATE**: Test Live Output panel independently with a running managed agent task
5. Deploy if ready — remaining stories are incremental improvements

### Incremental Delivery

1. Setup + Feature Flag → Foundation ready
2. Add US1 (core panel) → MVP
3. Add US2 (state feedback) → Production-ready
4. Add US3 (tab behavior) → Polished
5. Polish → Final

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- DOC-REQ-* IDs are included in task descriptions for traceability
- All implementation changes are in 2-3 existing files; no new files in the source tree
- The tmate web viewer handles terminal rendering, scrollback, and the rolling buffer natively
