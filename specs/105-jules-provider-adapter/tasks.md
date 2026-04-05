# Tasks: Jules Provider Adapter Runtime Alignment

**Input**: Design documents from `/specs/105-jules-provider-adapter/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Workflow-boundary and unit tests are required because this feature changes Temporal orchestration behavior, result semantics, and provider-to-workflow contracts.

**Organization**: Tasks are grouped by user story to keep bundled execution, truthful publication, and clarification-only follow-up behavior independently testable.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the bundle contract and test baseline for runtime changes.

- [ ] T001 Capture/update Jules workflow test fixtures in `tests/unit/workflows/temporal/workflows/test_run_integration.py` and `tests/unit/workflows/temporal/test_jules_activities.py` so the implementation can replace step-chained expectations deterministically (supports DOC-REQ-006, DOC-REQ-007, DOC-REQ-009, DOC-REQ-012)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add the shared runtime structures needed before any user-story-specific orchestration work.

- [X] T002 Create `moonmind/workflows/temporal/jules_bundle.py` with deterministic bundle eligibility, one-shot brief compilation, and manifest metadata helpers for `DOC-REQ-006`, `DOC-REQ-007`, and `DOC-REQ-008`
- [X] T003 Update `moonmind/workflows/temporal/worker_runtime.py` so Jules-related step planning preserves bundle-friendly metadata instead of assuming normal `jules_session_id` step chaining for `DOC-REQ-006` and `DOC-REQ-007`

**Checkpoint**: Bundle helpers and planner-side metadata are ready for workflow execution changes.

---

## Phase 3: User Story 1 - One-Shot Bundled Jules Execution (Priority: P1) 🎯 MVP

**Goal**: Consecutive Jules-targeted work executes as one synthetic bundle node and one provider session with a checklist-shaped brief.

**Independent Test**: A plan with multiple consecutive Jules nodes produces one bundled execution request, one provider session, preserved bundle metadata, and no normal continuation session handoff.

### Tests for User Story 1

- [X] T004 [P] [US1] Replace the current multi-step send-message workflow expectations in `tests/unit/workflows/temporal/workflows/test_run_integration.py` with one-shot bundle dispatch assertions covering `DOC-REQ-006`, `DOC-REQ-007`, and `DOC-REQ-008`

### Implementation for User Story 1

- [X] T005 [US1] Update `moonmind/workflows/temporal/workflows/run.py` to collapse consecutive Jules `agent_runtime` nodes into a synthetic bundled execution that uses `moonmind/workflows/temporal/jules_bundle.py` for `DOC-REQ-006`, `DOC-REQ-007`, and `DOC-REQ-008`
- [X] T006 [US1] Update `moonmind/workflows/temporal/workflows/agent_run.py` to remove normal `jules_session_id` continuation handling for bundled execution while retaining compact bundle result metadata for `DOC-REQ-008`, `DOC-REQ-009`, and `DOC-REQ-012`

**Checkpoint**: Standard Jules execution is bundle-first and no longer relies on normal step-to-step provider follow-up messages.

---

## Phase 4: User Story 2 - Truthful Branch Publication (Priority: P1)

**Goal**: `publishMode: "branch"` succeeds only when MoonMind can prove the requested PR/merge outcome actually landed on the target branch.

**Independent Test**: Workflow and activity tests cover successful merge, missing PR URL, base-update failure, merge rejection, and verification-failure paths with truthful final statuses.

### Tests for User Story 2

- [X] T007 [P] [US2] Add workflow-boundary failure-path coverage in `tests/unit/workflows/temporal/workflows/test_run_integration.py` and `tests/unit/workflows/temporal/test_run_artifacts.py` for `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-010`, and `DOC-REQ-011`
- [X] T008 [P] [US2] Extend `tests/unit/workflows/temporal/test_jules_merge_pr.py` and `tests/unit/workflows/temporal/test_jules_activities.py` to validate AUTO_CREATE_PR, base retargeting, merge failure handling, and explicit non-success mapping for `DOC-REQ-003`, `DOC-REQ-004`, and `DOC-REQ-005`

### Implementation for User Story 2

- [X] T009 [US2] Update `moonmind/workflows/temporal/workflows/run.py` so branch-publication success requires PR extraction, optional base retarget, merge completion, and verification-aware failure mapping for `DOC-REQ-004`, `DOC-REQ-005`, `DOC-REQ-010`, and `DOC-REQ-011`
- [X] T010 [US2] Update `moonmind/workflows/temporal/activities/jules_activities.py` and `moonmind/workflows/adapters/jules_agent_adapter.py` so Jules start/publish metadata preserves AUTO_CREATE_PR behavior and returns explicit branch-publication failure details for `DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-003`, `DOC-REQ-004`, `DOC-REQ-005`, and `DOC-REQ-011`

**Checkpoint**: Branch publication outcomes are MoonMind-owned and truthful.

---

## Phase 5: User Story 3 - Clarification-Only Follow-Up Messaging (Priority: P2)

**Goal**: `sendMessage` stays available for clarification/intervention/resume flows without being the standard progression mechanism for bundled Jules execution.

**Independent Test**: Normal bundled runs never call `integration.jules.send_message`, while clarification/auto-answer flows still do.

### Tests for User Story 3

- [X] T011 [P] [US3] Update clarification/resume-path tests in `tests/unit/workflows/temporal/test_jules_activities.py` and `tests/unit/workflows/temporal/workflows/test_run_integration.py` so only exception flows invoke `integration.jules.send_message` for `DOC-REQ-009` and `DOC-REQ-012`

### Implementation for User Story 3

- [X] T012 [US3] Refine `moonmind/workflows/temporal/workflows/agent_run.py` and `moonmind/workflows/temporal/activities/jules_activities.py` so clarification auto-answer remains the valid `sendMessage` path while bundled execution cannot route normal work through it for `DOC-REQ-009` and `DOC-REQ-012`

**Checkpoint**: Clarification still works, but normal multi-step chaining is gone.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finish regression coverage, remove obsolete test assumptions, and validate the full runtime diff.

- [X] T013 [P] Update `tests/provider/jules/test_jules_integration.py` to replace or retire the obsolete real-provider multi-step `sendMessage` lifecycle expectation in favor of one-shot bundled execution coverage for `DOC-REQ-006` and `DOC-REQ-009`
- [X] T014 [P] Run `./tools/test_unit.sh` and fix any workflow/adapter regressions introduced across `moonmind/workflows/temporal/` and `tests/unit/workflows/temporal/` to validate all `DOC-REQ-*`
- [X] T015 [P] Run `bash ".specify/scripts/bash/validate-implementation-scope.sh" --check diff --mode runtime --base-ref origin/main` and confirm the final diff includes runtime changes under `moonmind/` plus test coverage under `tests/`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: Starts immediately.
- **Phase 2**: Depends on Phase 1 and blocks all user stories.
- **Phase 3**: Depends on Phase 2.
- **Phase 4**: Depends on Phase 3 because truthful publish handling consumes bundle/result semantics.
- **Phase 5**: Depends on Phase 3 and can overlap Phase 4 after bundle-first execution exists.
- **Phase 6**: Depends on all implementation phases being complete.

### User Story Dependencies

- **US1**: No dependency on later stories; it is the MVP.
- **US2**: Depends on US1 bundle/result behavior.
- **US3**: Depends on US1 removal of normal session chaining.

### Within Each User Story

- Tests should fail against the old multi-step behavior before the new runtime logic is finished.
- Workflow/orchestration changes should land before cleanup of obsolete integration expectations.
- Final validation must use the repo’s canonical unit-test and scope-validation commands.

### Parallel Opportunities

- `T004`, `T007`, `T008`, and `T011` can be developed in parallel once foundational work is ready.
- `T009` and `T012` can proceed in parallel after `T005`/`T006`.
- `T013`, `T014`, and `T015` are parallelizable after implementation stabilizes.

---

## Parallel Example: User Story 2

```bash
Task: "Add workflow-boundary failure-path coverage in tests/unit/workflows/temporal/workflows/test_run_integration.py and tests/unit/workflows/temporal/test_run_artifacts.py"
Task: "Extend tests/unit/workflows/temporal/test_jules_merge_pr.py and tests/unit/workflows/temporal/test_jules_activities.py for AUTO_CREATE_PR and merge failure handling"
```

---

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational phases.
2. Finish US1 so standard Jules execution is bundle-first.
3. Validate that normal `sendMessage` progression is gone before moving on.

### Incremental Delivery

1. Bundle consecutive Jules work into one execution node.
2. Make branch publication truthful and verification-aware.
3. Preserve clarification-only follow-up messaging.
4. Replace obsolete integration expectations and run full validation.

### Notes

- Every `DOC-REQ-*` is covered by at least one implementation task and at least one validation task.
- Runtime scope minimums are satisfied by `moonmind/workflows/temporal/` changes plus test tasks under `tests/`.
- Do not reintroduce a hidden compatibility path for standard multi-step Jules session chaining.
