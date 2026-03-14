# Tasks: Worker Self-Heal System (Phase 1)

**Input**: Design documents from `/specs/034-worker-self-heal/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Required for this feature. Include unit coverage for retry recovery, retry exhaustion, deterministic fail-fast behavior, artifacts, control compatibility, and redaction.

**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Align feature artifacts and runtime guardrails with current Phase 1 strategy before coding changes.

- [X] T001 Align self-heal budget/default configuration wiring in `celery_worker/agentkit_worker.py` and `moonmind/agents/codex_worker/self_heal.py` for DOC-REQ-002 and DOC-REQ-013.
- [X] T002 [P] Refresh phase-aligned requirement mapping in `specs/034-worker-self-heal/spec.md` and `specs/034-worker-self-heal/contracts/requirements-traceability.md` with explicit DOC-REQ-005/DOC-REQ-006/DOC-REQ-009 deferred status.
- [X] T003 [P] Update runtime verification steps in `specs/034-worker-self-heal/quickstart.md` to require `./tools/test_unit.sh` and scope validation gates for DOC-REQ-013.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core self-heal primitives, telemetry, and artifact infrastructure required by all user stories.

**⚠️ CRITICAL**: No user story implementation starts until this phase is complete.

- [X] T004 Implement/normalize `SelfHealConfig`, `StepAttemptState`, failure signature hashing, and idle-timeout primitives in `moonmind/agents/codex_worker/self_heal.py` for DOC-REQ-001, DOC-REQ-002, and DOC-REQ-003.
- [X] T005 [P] Implement self-heal metric emitters and tag schema in `moonmind/agents/codex_worker/metrics.py` for DOC-REQ-010 and DOC-REQ-011.
- [X] T006 [P] Implement artifact path/storage helpers for step and self-heal state files in `moonmind/workflows/agent_queue/storage.py` for DOC-REQ-007.
- [X] T007 Wire shared codex worker exports for self-heal + metrics contracts in `moonmind/agents/codex_worker/__init__.py` for DOC-REQ-013 runtime integration.
- [X] T008 Add foundational config/primitive validation tests in `tests/unit/agents/codex_worker/test_self_heal.py` for DOC-REQ-001 and DOC-REQ-002.
- [X] T009 [P] Add foundational telemetry validation tests in `tests/unit/agents/codex_worker/test_metrics.py` for DOC-REQ-011.

**Checkpoint**: Foundation ready. User story work can begin.

---

## Phase 3: User Story 1 - Automatic Stuck-Step Recovery (Priority: P1) 🎯 MVP

**Goal**: Recover retryable step failures in-step using bounded retries and escalate only after exhaustion.

**Independent Test**: Simulate transient/retryable failures and verify attempt-failed/triggered/recovery events, then simulate retryable exhaustion and verify queue-retryable signaling.

### Tests for User Story 1

- [X] T010 [P] [US1] Add worker tests for wall-timeout, idle-timeout, and no-progress stuck detection in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-001.
- [X] T011 [P] [US1] Add worker tests for retryable vs deterministic classification and fail-fast behavior in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-003 and DOC-REQ-013.
- [X] T012 [P] [US1] Add worker tests for soft-reset recovery and retryable exhaustion queue escalation in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-004, DOC-REQ-008, and DOC-REQ-010.

### Implementation for User Story 1

- [X] T013 [US1] Implement bounded codex step attempt loop with `soft_reset` retries in `moonmind/agents/codex_worker/worker.py` for DOC-REQ-004 and DOC-REQ-013.
- [X] T014 [US1] Implement wall/idle/no-progress detection integration between `moonmind/agents/codex_worker/worker.py` and `moonmind/agents/codex_worker/self_heal.py` for DOC-REQ-001.
- [X] T015 [US1] Implement failure classification and retry policy routing in `moonmind/agents/codex_worker/worker.py` for DOC-REQ-003.
- [X] T016 [US1] Implement self-heal lifecycle events and queue retry metadata (`run_quality_reason.category=self_heal`) in `moonmind/agents/codex_worker/worker.py` for DOC-REQ-008 and DOC-REQ-010.
- [X] T017 [US1] Implement redaction of failure signatures and emitted payload fields in `moonmind/agents/codex_worker/worker.py` and `moonmind/agents/codex_worker/publish_sanitization.py` for DOC-REQ-012.

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Deterministic Checkpoint Metadata (Priority: P2)

**Goal**: Persist deterministic step and attempt artifacts that are stable inputs for later replay phases.

**Independent Test**: Verify successful step checkpoints and failed-attempt self-heal artifacts are written with required fields, hashes, and redacted summaries.

### Tests for User Story 2

- [X] T018 [P] [US2] Add checkpoint artifact content tests in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-007.
- [X] T019 [P] [US2] Add self-heal attempt artifact content tests in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-007 and DOC-REQ-010.
- [X] T020 [P] [US2] Add artifact storage helper tests in `tests/unit/workflows/agent_queue/test_artifact_storage.py` for DOC-REQ-007.
- [X] T021 [P] [US2] Add deferred-scope regression tests in `tests/unit/agents/codex_worker/test_worker.py` asserting retry attempts continue to reuse base step instructions while DOC-REQ-006 remains gated to Phase 2+.

### Implementation for User Story 2

- [X] T022 [US2] Persist successful step checkpoint artifacts under `state/steps/` in `moonmind/agents/codex_worker/worker.py` for DOC-REQ-007.
- [X] T023 [US2] Persist per-attempt self-heal artifacts under `state/self_heal/` in `moonmind/agents/codex_worker/worker.py` and `moonmind/workflows/agent_queue/storage.py` for DOC-REQ-007.
- [X] T024 [US2] Emit self-heal StatsD counters/timers with stable tags/outcomes in `moonmind/agents/codex_worker/worker.py` and `moonmind/agents/codex_worker/metrics.py` for DOC-REQ-011.
- [X] T025 [US2] Preserve Phase 1 behavior in `moonmind/agents/codex_worker/worker.py` (no retry-prompt envelope mutation yet) and keep DOC-REQ-006 activation criteria explicit in Phase 2+ traceability/docs.

**Checkpoint**: User Stories 1 and 2 work independently with persistent artifacts/metrics.

---

## Phase 5: User Story 3 - Operator Compatibility During Retries (Priority: P3)

**Goal**: Ensure self-heal retries preserve pause/takeover/cancel semantics and current control-surface boundaries.

**Independent Test**: During retry conditions, verify pause/takeover blocks next-attempt launch and cancel stops execution without additional retries.

### Tests for User Story 3

- [X] T026 [P] [US3] Add pause/takeover gating tests in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-008.
- [X] T027 [P] [US3] Add cancellation short-circuit tests in `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-008.
- [X] T028 [P] [US3] Add API/service tests rejecting recovery actions (`retry_step`, `hard_reset_step`, `resume_from_step`) in `tests/unit/api/routers/test_task_runs.py` and `tests/unit/workflows/agent_queue/test_service.py` for DOC-REQ-005 and DOC-REQ-009.

### Implementation for User Story 3

- [X] T029 [US3] Enforce pause/takeover/cancel checks at each self-heal attempt boundary in `moonmind/agents/codex_worker/worker.py` for DOC-REQ-008.
- [X] T030 [US3] Keep task-run control action allowlist (`pause`, `resume`, `takeover`) in `moonmind/workflows/agent_queue/service.py` and `api_service/api/routers/task_runs.py` for DOC-REQ-009.
- [X] T031 [US3] Keep recovery-action deferrals explicit in `specs/034-worker-self-heal/contracts/task-run-recovery.openapi.yaml` and `specs/034-worker-self-heal/contracts/requirements-traceability.md` for DOC-REQ-005 and DOC-REQ-009.

**Checkpoint**: All three user stories are independently testable and control-compatible.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final hardening, full-suite validation, and requirement traceability closure.

- [X] T032 [P] Add regression tests for secret scrubbing edge cases in `tests/unit/agents/codex_worker/test_self_heal.py` and `tests/unit/agents/codex_worker/test_worker.py` for DOC-REQ-012.
- [X] T033 Validate DOC-REQ-001 through DOC-REQ-013 coverage rows and status text in `specs/034-worker-self-heal/contracts/requirements-traceability.md`.
- [X] T034 Run runtime tasks gate `SPECIFY_FEATURE=034-worker-self-heal .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and fix gaps in `specs/034-worker-self-heal/tasks.md` for DOC-REQ-013.
- [X] T035 Run required unit suite `./tools/test_unit.sh` and record final verification notes in `specs/034-worker-self-heal/quickstart.md` for DOC-REQ-013.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all user stories.
- **Phase 3 (US1)**: Depends on Phase 2.
- **Phase 4 (US2)**: Depends on Phase 2 and integrates with US1 artifact/event contracts.
- **Phase 5 (US3)**: Depends on Phase 2 and validates compatibility with US1 retry loop.
- **Phase 6 (Polish)**: Depends on completion of selected user stories.

### User Story Dependencies

- **US1 (P1)**: MVP; no dependency on other stories once foundational work is complete.
- **US2 (P2)**: Depends on US1 event/attempt loop semantics for artifact parity checks.
- **US3 (P3)**: Depends on US1 retry-loop behavior to verify pause/takeover/cancel boundaries.

### Within Each User Story

- Story tests are written before story implementation and should fail initially.
- Retry/control primitives must exist before event/artifact enrichment.
- Runtime implementation completes before full-suite validation.

---

## Parallel Opportunities

- T002 and T003 can run in parallel during setup.
- T005, T006, and T009 can run in parallel in foundational work.
- US1 test tasks T010-T012 can run in parallel.
- US2 test tasks T018-T021 can run in parallel.
- US3 test tasks T026-T028 can run in parallel.
- Polish tasks T032 and T033 can run in parallel before final gates T034-T035.

---

## Parallel Example: User Story 1

```bash
# Parallel US1 tests
Task: "T010 [US1] Add worker tests for timeout/no-progress stuck detection in tests/unit/agents/codex_worker/test_worker.py"
Task: "T011 [US1] Add worker tests for classification/fail-fast behavior in tests/unit/agents/codex_worker/test_worker.py"
Task: "T012 [US1] Add worker tests for soft-reset recovery and exhaustion escalation in tests/unit/agents/codex_worker/test_worker.py"
```

## Parallel Example: User Story 2

```bash
# Parallel US2 artifact validations
Task: "T018 [US2] Add checkpoint artifact content tests in tests/unit/agents/codex_worker/test_worker.py"
Task: "T019 [US2] Add self-heal attempt artifact content tests in tests/unit/agents/codex_worker/test_worker.py"
Task: "T020 [US2] Add artifact storage helper tests in tests/unit/workflows/agent_queue/test_artifact_storage.py"
Task: "T021 [US2] Add deferred-scope regression tests in tests/unit/agents/codex_worker/test_worker.py"
```

## Parallel Example: User Story 3

```bash
# Parallel US3 control-compatibility validations
Task: "T026 [US3] Add pause/takeover gating tests in tests/unit/agents/codex_worker/test_worker.py"
Task: "T027 [US3] Add cancellation short-circuit tests in tests/unit/agents/codex_worker/test_worker.py"
Task: "T028 [US3] Add API/service tests rejecting deferred recovery actions in tests/unit/api/routers/test_task_runs.py and tests/unit/workflows/agent_queue/test_service.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Complete Phase 3 (US1).
3. Validate US1 independently with T010-T012 and relevant runtime checks.
4. Demo/deploy MVP behavior for in-step self-heal.

### Incremental Delivery

1. Ship US1 retry recovery.
2. Add US2 artifact determinism and telemetry validation.
3. Add US3 control compatibility and deferred action guardrails.
4. Finish with Phase 6 cross-cutting validation and required gates.

### Parallel Team Strategy

1. One engineer handles foundational runtime primitives (T004-T007).
2. One engineer drives US1/US3 worker-loop and control compatibility tasks.
3. One engineer drives US2 artifact/metrics tasks and test coverage.
4. Merge at Phase 6 for final validation gates.
