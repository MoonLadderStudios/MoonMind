# Tasks: Jules Question Auto-Answer

**Input**: Design documents from `/specs/094-jules-auto-answer/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/requirements-traceability.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)
- Exact file paths included in descriptions

---

## Phase 1: Setup

**Purpose**: No new project setup needed — this feature modifies existing modules.

- [ ] T001 Create feature branch `094-jules-auto-answer` from main (done)

---

## Phase 2: Foundational — Schema & Status Normalization

**Purpose**: Core type changes that ALL user stories depend on. Must complete before workflow logic.

**⚠️ CRITICAL**: No workflow or activity work can begin until these tasks are complete.

- [ ] T002 [P] Add `"awaiting_feedback"` to `JulesNormalizedStatus` literal in `moonmind/schemas/jules_models.py` (DOC-REQ-001)
- [ ] T003 [P] Update `_JULES_STATUS_MAP` to map `"awaiting_user_feedback"` → `"awaiting_feedback"` in `moonmind/schemas/jules_models.py` (DOC-REQ-001)
- [ ] T004 [P] Add `"awaiting_feedback"` to `JulesNormalizedStatus` literal in `moonmind/jules/status.py` and add `"awaiting_user_feedback"` → `"awaiting_feedback"` mapping (DOC-REQ-001)
- [ ] T005 [P] Add `JulesAgentMessage` Pydantic model in `moonmind/schemas/jules_models.py` with `agent_message: str` field (DOC-REQ-010)
- [ ] T006 [P] Add `JulesActivity` Pydantic model in `moonmind/schemas/jules_models.py` with union activity fields including `agent_messaged: Optional[JulesAgentMessage]` (DOC-REQ-010)
- [ ] T007 [P] Add `JulesListActivitiesResult` Pydantic model in `moonmind/schemas/jules_models.py` with `latest_agent_question`, `activity_id`, `session_id` (DOC-REQ-010)
- [ ] T008 Add `"awaiting_feedback"` to `_EXTERNAL_STATUS_TO_RUN_STATUS` mapping in `moonmind/workflows/temporal/workflows/agent_run.py`
- [ ] T009 Write unit tests for status normalization changes in `tests/unit/schemas/test_jules_models.py` (DOC-REQ-001 validation)
- [ ] T010 Write unit tests for new Pydantic models in `tests/unit/schemas/test_jules_models.py` (DOC-REQ-010 validation)

**Checkpoint**: Status normalization and schema models ready. Workflow and activity tasks can begin.

---

## Phase 3: User Story 1 — Workflow Automatically Answers Jules Question (Priority: P1) 🎯 MVP

**Goal**: When Jules asks a question, MoonMind detects it, generates an LLM answer, and sends it back.

**Independent Test**: Start a Jules session that triggers `AWAITING_USER_FEEDBACK`, verify auto-answer sub-flow fires and answer is delivered.

### Implementation for User Story 1

- [ ] T011 [US1] Add `list_activities(session_id: str)` method to `JulesClient` in `moonmind/workflows/adapters/jules_client.py` calling `GET /v1alpha/sessions/{id}/activities` (DOC-REQ-002)
- [ ] T012 [US1] Implement `integration.jules.list_activities` Temporal activity in `moonmind/workflows/temporal/activities/jules_activities.py` — calls `JulesClient.list_activities()`, extracts latest `AgentMessaged.agentMessage`, returns `JulesListActivitiesResult` (DOC-REQ-002, DOC-REQ-011)
- [ ] T013 [US1] Implement `integration.jules.answer_question` Temporal activity in `moonmind/workflows/temporal/activities/jules_activities.py` — orchestrates list_activities → LLM prompt → send_message (DOC-REQ-003, DOC-REQ-012)
- [ ] T014 [US1] Register both new activities in `moonmind/workflows/temporal/activity_catalog.py` on `mm.activity.integrations` queue (DOC-REQ-011, DOC-REQ-012)
- [ ] T015 [US1] Add activity handler methods in `moonmind/workflows/temporal/activity_runtime.py` for `integration.jules.list_activities` and `integration.jules.answer_question`
- [ ] T016 [US1] Add auto-answer sub-flow to `MoonMind.AgentRun` polling loop in `moonmind/workflows/temporal/workflows/agent_run.py` — detect `awaiting_feedback`, call `integration.jules.list_activities`, dispatch to LLM, call `integration.jules.send_message` (DOC-REQ-004, DOC-REQ-005)
- [ ] T017 [US1] Add auto-answer sub-flow to `MoonMind.Run._run_integration_stage()` integration polling in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-005)
- [ ] T018 [US1] Write unit tests for `JulesClient.list_activities()` transport in `tests/unit/workflows/test_jules_client.py` (DOC-REQ-002 validation)
- [ ] T019 [US1] Write unit tests for `integration.jules.list_activities` activity in `tests/unit/workflows/test_jules_activities.py` (DOC-REQ-011 validation)
- [ ] T020 [US1] Write unit tests for `integration.jules.answer_question` activity in `tests/unit/workflows/test_jules_activities.py` (DOC-REQ-012 validation)
- [ ] T021 [US1] Write unit tests for auto-answer sub-flow in `MoonMind.AgentRun` in `tests/unit/workflows/test_agent_run_auto_answer.py` (DOC-REQ-004, DOC-REQ-005 validation)

**Checkpoint**: Core auto-answer flow works end-to-end.

---

## Phase 4: User Story 2 — Max Auto-Answer Cycles with Escalation (Priority: P1)

**Goal**: Enforce max question limit and escalate to `intervention_requested` when exhausted.

**Independent Test**: Set `JULES_MAX_AUTO_ANSWERS=1`, trigger two questions, verify second maps to `intervention_requested`.

### Implementation for User Story 2

- [ ] T022 [US2] Add `_auto_answer_count` workflow variable and max-cycle enforcement to auto-answer sub-flow in `moonmind/workflows/temporal/workflows/agent_run.py` (DOC-REQ-006)
- [ ] T023 [US2] Add `_auto_answer_count` and max-cycle enforcement to `MoonMind.Run._run_integration_stage()` in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-006)
- [ ] T024 [US2] Read `JULES_MAX_AUTO_ANSWERS` env var (default 3) via activity for determinism in `moonmind/workflows/temporal/activities/jules_activities.py` (DOC-REQ-009)
- [ ] T025 [US2] Write unit tests for max-cycle enforcement in `tests/unit/workflows/test_agent_run_auto_answer.py` (DOC-REQ-006 validation)

**Checkpoint**: Max-cycle guardrail operational with escalation.

---

## Phase 5: User Story 3 — Auto-Answer Disabled (Priority: P2)

**Goal**: When `JULES_AUTO_ANSWER_ENABLED=false`, skip auto-answer and escalate immediately.

**Independent Test**: Set env var to `false`, trigger `AWAITING_USER_FEEDBACK`, verify `intervention_requested` without LLM calls.

### Implementation for User Story 3

- [ ] T026 [US3] Read `JULES_AUTO_ANSWER_ENABLED` env var (default `true`) via activity in `moonmind/workflows/temporal/activities/jules_activities.py` (DOC-REQ-008, DOC-REQ-009)
- [ ] T027 [US3] Add opt-out check to auto-answer sub-flow in both `agent_run.py` and `run.py` (DOC-REQ-008)
- [ ] T028 [US3] Write unit tests for opt-out behavior in `tests/unit/workflows/test_agent_run_auto_answer.py` (DOC-REQ-008 validation)

**Checkpoint**: Opt-out toggle functional.

---

## Phase 6: User Story 4 — Question Deduplication (Priority: P2)

**Goal**: Prevent re-answering the same question on duplicate polls.

**Independent Test**: Return same activity ID across two polls, verify only one LLM call.

### Implementation for User Story 4

- [ ] T029 [US4] Add `_answered_activity_ids: set[str]` tracking to auto-answer sub-flow in `agent_run.py` and `run.py` (DOC-REQ-007)
- [ ] T030 [US4] Write unit tests for deduplication in `tests/unit/workflows/test_agent_run_auto_answer.py` (DOC-REQ-007 validation)

**Checkpoint**: Question deduplication prevents duplicate answers.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Config consolidation, timeout handling, and final validation.

- [ ] T031 [P] Read `JULES_AUTO_ANSWER_RUNTIME` (default `llm`) and `JULES_AUTO_ANSWER_TIMEOUT_SECONDS` (default `300`) env vars via activity (DOC-REQ-009)
- [ ] T032 [P] Add timeout enforcement to each auto-answer cycle in workflow polling loops
- [ ] T033 [P] Export new models in `moonmind/schemas/jules_models.py` `__all__` list
- [ ] T034 Run `./tools/test_unit.sh` and verify all tests pass
- [ ] T035 Run quickstart.md validation steps

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: Complete
- **Phase 2 (Foundational)**: No dependencies — start immediately
- **Phase 3 (US1)**: Depends on Phase 2 completion
- **Phase 4 (US2)**: Depends on Phase 3 (auto-answer sub-flow exists)
- **Phase 5 (US3)**: Depends on Phase 3 (auto-answer sub-flow exists)
- **Phase 6 (US4)**: Depends on Phase 3 (auto-answer sub-flow exists)
- **Phase 7 (Polish)**: Depends on Phases 3–6

### User Story Dependencies

- **US1 (P1)**: Core flow — no dependencies on other stories
- **US2 (P1)**: Requires US1 sub-flow to exist (adds max-cycle on top)
- **US3 (P2)**: Requires US1 sub-flow to exist (adds opt-out check before it)
- **US4 (P2)**: Requires US1 sub-flow to exist (adds dedup tracking within it)

### Parallel Opportunities

- T002–T007 are all parallel (different models/mappings in different files or non-overlapping sections)
- T009–T010 test tasks parallel
- T018–T021 test tasks parallel
- US2, US3, US4 implementation can proceed in parallel after US1 completes

---

## Implementation Strategy

### MVP First (P1 User Stories)

1. Complete Phase 2: Foundational schemas
2. Complete Phase 3: User Story 1 (core auto-answer flow)
3. **VALIDATE**: Run tests, verify end-to-end
4. Complete Phase 4: User Story 2 (max-cycle guardrail)
5. **DEPLOY**: MVP with core flow + safety guardrail

### Full Delivery

6. Phase 5: User Story 3 (opt-out)
7. Phase 6: User Story 4 (dedup)
8. Phase 7: Polish
9. Final test run and validation

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps to user stories from spec.md
- All production code changes are in `moonmind/` (runtime code)
- All validation tasks produce tests in `tests/unit/`
- DOC-REQ-* IDs annotated for traceability
