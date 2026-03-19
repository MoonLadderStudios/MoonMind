# Tasks: Step Review Gate

**Input**: Design documents from `/specs/086-step-review-gate/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

## Phase 1: Setup

**Purpose**: No project init needed — existing codebase. Setup is creating the new module files.

- [ ] T001 Create empty review gate module `moonmind/workflows/skills/review_gate.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-009, DOC-REQ-010)
- [ ] T002 [P] Create empty step review activity module `moonmind/workflows/temporal/activities/step_review.py` (DOC-REQ-011)

---

## Phase 2: Foundational (Data Model + Contracts)

**Purpose**: Define all data types and contracts that US1–US4 depend on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T003 Add `ReviewGatePolicy` dataclass to `moonmind/workflows/skills/tool_plan_contracts.py` with fields: `enabled` (default False), `max_review_attempts` (default 2), `reviewer_model` (default "default"), `review_timeout_seconds` (default 120), `skip_tool_types` (default ()) and validation (DOC-REQ-001)
- [ ] T004 Extend `PlanPolicy` in `moonmind/workflows/skills/tool_plan_contracts.py` to include optional `review_gate: ReviewGatePolicy` field with default `ReviewGatePolicy()` (DOC-REQ-002)
- [ ] T005 Update `parse_plan_definition()` in `moonmind/workflows/skills/tool_plan_contracts.py` to parse `policy.review_gate` JSON block; absent block must produce default disabled policy (DOC-REQ-003)
- [ ] T006 [P] Implement `ReviewRequest` dataclass in `moonmind/workflows/skills/review_gate.py` with fields: `node_id`, `step_index`, `total_steps`, `review_attempt`, `tool_name`, `tool_version`, `tool_type`, `inputs`, `execution_result`, `workflow_context`, `previous_feedback` (DOC-REQ-004)
- [ ] T007 [P] Implement `ReviewVerdict` dataclass in `moonmind/workflows/skills/review_gate.py` with fields: `verdict` (PASS/FAIL/INCONCLUSIVE), `confidence`, `feedback`, `issues`; validate verdict values (DOC-REQ-005)
- [ ] T008 [P] Implement `build_feedback_input()` in `moonmind/workflows/skills/review_gate.py` that injects `_review_feedback` dict into skill step inputs (DOC-REQ-009)
- [ ] T009 [P] Implement `build_feedback_instruction()` in `moonmind/workflows/skills/review_gate.py` that appends feedback text to agent_runtime instruction strings (DOC-REQ-010)
- [ ] T010 [P] Implement `build_review_prompt()` in `moonmind/workflows/skills/review_gate.py` that constructs the LLM review prompt from a `ReviewRequest` (DOC-REQ-012)
- [ ] T011 Write unit tests for `ReviewGatePolicy` validation in `tests/unit/workflows/skills/test_review_gate_contracts.py` (DOC-REQ-001 validation)
- [ ] T012 [P] Write unit tests for `ReviewRequest`, `ReviewVerdict`, feedback builders, and prompt builder in `tests/unit/workflows/skills/test_review_gate_contracts.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-009, DOC-REQ-010 validation)
- [ ] T013 [P] Write unit tests for `PlanPolicy` with/without `review_gate` and `parse_plan_definition()` with `review_gate` JSON in `tests/unit/workflows/skills/test_review_gate_policy.py` (DOC-REQ-002, DOC-REQ-003 validation)

**Checkpoint**: All data types defined and tested. Review activity and workflow loop can now proceed.

---

## Phase 3: User Story 1 — Enable Review Gate for a Workflow (Priority: P1) 🎯 MVP

**Goal**: A workflow with `review_gate.enabled: true` runs a review activity after each step.

**Independent Test**: Create a plan with review gate enabled, mock step execution and review activity, verify review runs after each step.

### Implementation for User Story 1

- [ ] T014 [US1] Implement `step_review_activity()` in `moonmind/workflows/temporal/activities/step_review.py` — accept `ReviewRequest`, call LLM fleet, parse response into `ReviewVerdict`, return as dict (DOC-REQ-011, DOC-REQ-012)
- [ ] T015 [US1] Register `step.review` activity route in `moonmind/workflows/temporal/activity_catalog.py` (DOC-REQ-011)
- [ ] T016 [US1] Add review-retry loop in `_run_execution_stage()` in `moonmind/workflows/temporal/workflows/run.py` — when `review_gate.enabled` and node not in `skip_tool_types`, execute review after step; on PASS/INCONCLUSIVE continue, on FAIL retry with feedback up to `max_review_attempts` (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-018)
- [ ] T017 [US1] Write unit tests for the review-retry loop in `tests/unit/workflows/temporal/test_run_review_gate.py` covering: review runs after each step, PASS proceeds, gate-disabled path is unchanged (DOC-REQ-006 validation)
- [ ] T018 [US1] Write unit tests for `step_review_activity()` with mocked LLM responses in `tests/unit/workflows/temporal/test_step_review_activity.py` (DOC-REQ-011, DOC-REQ-012 validation)

**Checkpoint**: Review gate runs after steps. PASS/FAIL/retry works. MVP complete.

---

## Phase 4: User Story 2 — Retry Failed Steps with Feedback (Priority: P1)

**Goal**: Failed reviews inject structured feedback and the step self-corrects on retry.

**Independent Test**: Mock a FAIL→PASS sequence, verify feedback injected into step inputs.

### Implementation for User Story 2

- [ ] T019 [US2] Implement feedback injection in the review-retry loop in `moonmind/workflows/temporal/workflows/run.py` — call `build_feedback_input()` for skill steps and `build_feedback_instruction()` for agent_runtime steps before retry (DOC-REQ-009, DOC-REQ-010)
- [ ] T020 [US2] Write unit tests for feedback injection in `tests/unit/workflows/temporal/test_run_review_gate.py` covering: FAIL→retry with `_review_feedback` in inputs, agent_runtime instruction append, max retries exhausted (DOC-REQ-009, DOC-REQ-010, DOC-REQ-007 validation)
- [ ] T021 [US2] Write unit tests for INCONCLUSIVE treated as PASS in `tests/unit/workflows/temporal/test_run_review_gate.py` (DOC-REQ-008 validation)
- [ ] T022 [US2] Write unit tests for FAIL_FAST and CONTINUE failure mode interaction with review gate in `tests/unit/workflows/temporal/test_run_review_gate.py` (DOC-REQ-017 validation)

**Checkpoint**: Full retry-with-feedback loop works with both failure modes.

---

## Phase 5: User Story 3 — Configure Review Gate Parameters (Priority: P2)

**Goal**: Configuration precedence (plan > workflow > env > default) works correctly.

**Independent Test**: Set config at different levels and verify correct resolution.

### Implementation for User Story 3

- [ ] T023 [US3] Implement configuration precedence resolver in `moonmind/workflows/temporal/workflows/run.py` — merge plan-level `review_gate`, workflow-level `initialParameters.reviewGate`, and `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED` env var with correct precedence (DOC-REQ-013, DOC-REQ-014)
- [ ] T024 [US3] Write unit tests for configuration precedence in `tests/unit/workflows/temporal/test_run_review_gate.py` covering: plan-level wins, workflow-level when plan omits, env var when both omit, default off (DOC-REQ-013, DOC-REQ-014 validation)

**Checkpoint**: All configuration paths resolved and tested.

---

## Phase 6: User Story 4 — Observe Review Results (Priority: P2)

**Goal**: Operators see review verdicts in workflow memo and finish summary.

**Independent Test**: Verify memo format and finish summary shape after review-gated execution.

### Implementation for User Story 4

- [ ] T025 [US4] Add memo updates during review cycle in `moonmind/workflows/temporal/workflows/run.py` — update summary to show "Step N/M: tool_name (review attempt X/Y)" (DOC-REQ-015)
- [ ] T026 [US4] Add `reviewGate` metrics object to finish summary in `moonmind/workflows/temporal/workflows/run.py` — track `stepsReviewed`, `totalReviewAttempts`, `passedFirstAttempt`, `passedAfterRetry`, `failedAfterMaxRetries` (DOC-REQ-016)
- [ ] T027 [US4] Write unit tests for memo format and finish summary shape in `tests/unit/workflows/temporal/test_run_review_gate.py` (DOC-REQ-015, DOC-REQ-016 validation)

**Checkpoint**: All observability features working and tested.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T028 [P] Add `ReviewGatePolicy` to `__all__` exports in `moonmind/workflows/skills/tool_plan_contracts.py`
- [ ] T029 [P] Add review gate module exports to `moonmind/workflows/skills/review_gate.py` `__all__`
- [ ] T030 Run full unit test suite via `./tools/test_unit.sh` and verify all tests pass
- [ ] T031 Verify Temporal determinism: confirm no env var reads or nondeterministic operations in workflow code (only in activities) (DOC-REQ-019)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — create empty modules
- **Phase 2 (Foundational)**: Depends on Phase 1 — defines all contracts and data types
- **Phase 3 (US1)**: Depends on Phase 2 — implements core review loop
- **Phase 4 (US2)**: Depends on Phase 3 — adds feedback injection to existing loop
- **Phase 5 (US3)**: Depends on Phase 2 — config precedence is independent of loop implementation
- **Phase 6 (US4)**: Depends on Phase 3 — adds observability to existing loop
- **Phase 7 (Polish)**: Depends on all prior phases

### Parallel Opportunities

- T001 and T002 can run in parallel (different files)
- T006, T007, T008, T009, T010 can run in parallel (same file but different classes/functions)
- T011, T012, T013 can run in parallel (different test files)
- Phase 5 (US3) and Phase 6 (US4) can run in parallel once Phase 3 is complete

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational contracts
3. Complete Phase 3: US1 — review gate works with PASS/FAIL
4. **STOP and VALIDATE**: Run `./tools/test_unit.sh`

### Incremental Delivery

1. Setup + Foundational → Contracts validated
2. US1 → Review loop works → MVP
3. US2 → Feedback injection works → Full retry
4. US3 → Config precedence works → Operator control
5. US4 → Observability works → Production ready

---

## Notes

- Total tasks: 31
- US1: 5 tasks, US2: 4 tasks, US3: 2 tasks, US4: 3 tasks
- All DOC-REQ-* IDs have implementation and validation tasks
- Tests included per spec requirement for validation coverage
