# Tasks: Jules Temporal External Events

**Input**: Design documents from `/specs/048-jules-external-events/`  
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `data-model.md`, `contracts/`, `quickstart.md`  
**Tests**: Validation tests are required because `FR-001`, `FR-017`, and `DOC-REQ-017` require production runtime code plus automated verification.  
**Organization**: Tasks are grouped by user story so each story can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no unmet dependencies)
- **[Story]**: User story label (`[US1]`, `[US2]`, `[US3]`) for story-phase tasks only
- Every task includes exact file path(s) and `DOC-REQ-*` tags for traceability

## Prompt B Scope Controls (Step 8/17)

- Runtime implementation tasks are explicitly represented in `T001-T007`, `T012-T015`, `T020-T024`, and `T029-T032`.
- Runtime validation tasks are explicitly represented in `T008-T011`, `T016-T019`, `T025-T028`, and `T034-T037`.
- `DOC-REQ-001` through `DOC-REQ-017` retain at least one implementation task and one validation task in this file.
- Runtime-mode completion requires production file changes plus automated validation; docs-only completion is non-compliant.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare shared Jules schemas, gate plumbing, and artifact helper surfaces before story work begins.

- [X] T001 Add Jules Temporal semantic request/response fields and validation helpers in `moonmind/schemas/jules_models.py` and `moonmind/config/jules_settings.py` (DOC-REQ-002, DOC-REQ-008, DOC-REQ-009).
- [X] T002 [P] Centralize Jules runtime-gate plumbing and canonical disabled-state helpers in `moonmind/jules/runtime.py` and `moonmind/config/settings.py` (DOC-REQ-003).
- [X] T003 [P] Prepare Jules tracking/result artifact helper exports in `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/__init__.py` (DOC-REQ-005, DOC-REQ-010, DOC-REQ-014).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Build the shared Jules runtime primitives that every user story depends on.

**⚠️ CRITICAL**: Complete this phase before starting user story implementation.

- [X] T004 Implement centralized Jules status alias mapping, terminal helpers, and `unknown` fallback in `moonmind/jules/status.py` (DOC-REQ-002, DOC-REQ-006).
- [X] T005 Preserve Jules create/get/resolve transport semantics, metadata handling, retries, and scrubbed error behavior in `moonmind/workflows/adapters/jules_client.py` and `moonmind/schemas/jules_models.py` (DOC-REQ-004, DOC-REQ-015).
- [X] T006 [P] Lock `integration.jules.start`, `integration.jules.status`, and `integration.jules.fetch_result` to `mm.activity.integrations` and keep `integration.jules.cancel` reserved in `moonmind/workflows/temporal/activity_catalog.py` and `moonmind/workflows/temporal/workers.py` (DOC-REQ-007, DOC-REQ-011).
- [X] T007 Implement shared Jules activity snapshot/result helpers for compact outputs and artifact-backed tracking in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-005, DOC-REQ-010, DOC-REQ-014).

**Checkpoint**: Shared gate, status, adapter, queue, and artifact primitives are ready; user stories can proceed.

---

## Phase 3: User Story 1 - Start Jules-Backed Work with Stable Correlation (Priority: P1) 🎯 MVP

**Goal**: Start Jules-backed work only when the shared gate is satisfied and return stable MoonMind correlation plus bounded provider identity fields.

**Independent Test**: Submit one valid Jules-backed request and one invalid Jules-backed request, then verify early rejection when the gate is unsatisfied and compact correlation/provider identity fields when the request is accepted.

### Tests for User Story 1

- [X] T008 [P] [US1] Add shared runtime-gate coverage for missing-config rejection and canonical error messaging in `tests/unit/jules/test_jules_runtime.py` (DOC-REQ-003).
- [X] T009 [P] [US1] Add API and dashboard runtime-gate coverage for Jules availability, runtime hiding, and compatibility identity fields in `tests/unit/api/routers/test_agent_queue.py`, `tests/unit/api/routers/test_mcp_tools.py`, and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-016).
- [X] T010 [P] [US1] Add Temporal start-contract, shared-gate rejection, idempotent start, compact provider identity, and queue-topology coverage in `tests/unit/workflows/temporal/test_activity_runtime.py` and `tests/contract/test_temporal_activity_topology.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-005, DOC-REQ-007, DOC-REQ-008).
- [X] T011 [P] [US1] Add CLI and worker preflight coverage for Jules runtime selection, start gating, and workflow-vs-provider identity separation in `tests/unit/agents/codex_worker/test_cli.py` and `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-016, DOC-REQ-017).

### Implementation for User Story 1

- [X] T012 [US1] Enforce the shared Jules gate and early `targetRuntime=jules` rejection in `api_service/api/routers/agent_queue.py`, `moonmind/agents/codex_worker/cli.py`, and `moonmind/agents/codex_worker/worker.py` (DOC-REQ-003, DOC-REQ-017).
- [X] T013 [US1] Hide Jules MCP tools and dashboard runtime options when the shared gate is unsatisfied in `api_service/api/routers/mcp_tools.py`, `moonmind/mcp/jules_tool_registry.py`, and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-016).
- [X] T014 [US1] Implement `integration.jules.start` shared-gate enforcement, correlation, idempotency, artifact-backed description sourcing, and compact start output mapping in `moonmind/workflows/temporal/activity_runtime.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-005, DOC-REQ-008).
- [X] T015 [US1] Preserve MoonMind-owned correlation records and provider-handle separation in `moonmind/workflows/temporal/activity_runtime.py`, `moonmind/schemas/jules_models.py`, and `moonmind/agents/codex_worker/worker.py` (DOC-REQ-001, DOC-REQ-005, DOC-REQ-016).

**Checkpoint**: User Story 1 delivers the MVP start path with stable correlation and bounded provider identity.

---

## Phase 4: User Story 2 - Monitor Jules Progress and Materialize Results Safely (Priority: P1)

**Goal**: Monitor Jules work through polling today, keep callback behavior future-ready but disabled, and materialize terminal outputs as compact artifact-backed results.

**Independent Test**: Drive a Jules-backed run through aliased, unknown, non-terminal, and terminal provider states, then verify shared normalization, compact polling results, and artifact-backed terminal materialization.

### Tests for User Story 2

- [X] T016 [P] [US2] Add shared status-normalization coverage for aliased statuses, empty statuses, raw-status preservation, and `unknown` fallback in `tests/unit/jules/test_status.py` (DOC-REQ-002, DOC-REQ-006).
- [X] T017 [P] [US2] Add Jules adapter retry, fail-fast `4xx`, and secret-safe error coverage in `tests/unit/workflows/adapters/test_jules_client.py` (DOC-REQ-004, DOC-REQ-015).
- [X] T018 [P] [US2] Add Temporal status, fetch-result, callback-disabled/future-`ExternalEvent` contract, polling fallback, artifact-materialization, and secret-safe runtime error coverage in `tests/unit/workflows/temporal/test_activity_runtime.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-012, DOC-REQ-013, DOC-REQ-014, DOC-REQ-015).
- [X] T019 [P] [US2] Add legacy worker polling and shared-normalizer reuse coverage in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-012).

### Implementation for User Story 2

- [X] T020 [US2] Finish the shared Jules status normalizer and terminal-state helpers in `moonmind/jules/status.py` (DOC-REQ-002, DOC-REQ-006, DOC-REQ-009).
- [X] T021 [US2] Implement read-only `integration.jules.status` compact monitoring output, raw-status preservation, and retry-safe polling behavior in `moonmind/workflows/temporal/activity_runtime.py` (DOC-REQ-004, DOC-REQ-006, DOC-REQ-009, DOC-REQ-012).
- [X] T022 [US2] Implement conservative `integration.jules.fetch_result` terminal snapshot, summary, and diagnostics artifact materialization in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/artifacts.py` (DOC-REQ-010, DOC-REQ-014).
- [X] T023 [US2] Reuse shared Jules polling/backoff semantics and keep `callback_supported=false` across `moonmind/agents/codex_worker/worker.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/workflows/adapters/jules_client.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-012).
- [X] T024 [US2] Keep future Jules callback handling bounded, authenticated/dedupe-ready by contract, and disabled until ingress exists in `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/schemas/jules_models.py` (DOC-REQ-008, DOC-REQ-013).

**Checkpoint**: User Story 2 delivers compact polling-based monitoring and conservative terminal result materialization.

---

## Phase 5: User Story 3 - Preserve Truthful Cancellation, Security, and UI Identity (Priority: P2)

**Goal**: Keep cancellation claims truthful, secrets scrubbed, and MoonMind workflow identity primary in API, dashboard, and worker compatibility views.

**Independent Test**: Cancel a Jules-backed run and inspect API, dashboard, worker, and MCP-facing summaries to verify provider cancellation is reported as unsupported, secrets remain scrubbed, and Jules `taskId` never replaces MoonMind execution identity.

### Tests for User Story 3

- [X] T025 [P] [US3] Add truthfully unsupported cancel and diagnostics-artifact coverage in `tests/unit/workflows/temporal/test_activity_runtime.py` and `tests/contract/test_temporal_activity_topology.py` (DOC-REQ-007, DOC-REQ-011, DOC-REQ-014).
- [X] T026 [P] [US3] Add API and dashboard compatibility identity coverage for MoonMind-primary handles and provider-handle separation in `tests/unit/api/routers/test_agent_queue.py` and `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-016).
- [X] T027 [P] [US3] Add worker and CLI compatibility coverage for cancellation summaries, runtime selection, and provider-handle presentation in `tests/unit/agents/codex_worker/test_cli.py` and `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-011, DOC-REQ-012, DOC-REQ-016).
- [X] T028 [P] [US3] Add Jules MCP registration and secret-scrubbing coverage in `tests/unit/mcp/test_jules_tool_registry.py` and `tests/unit/api/routers/test_mcp_tools.py` (DOC-REQ-003, DOC-REQ-015).

### Implementation for User Story 3

- [X] T029 [US3] Keep `integration.jules.cancel` reserved and report provider cancellation as unsupported in `moonmind/workflows/temporal/activity_catalog.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `moonmind/agents/codex_worker/worker.py` (DOC-REQ-007, DOC-REQ-011, DOC-REQ-012).
- [X] T030 [US3] Preserve scrubbed Jules error handling and no-secret logging across `moonmind/workflows/adapters/jules_client.py`, `moonmind/workflows/temporal/activity_runtime.py`, and `api_service/api/routers/mcp_tools.py` (DOC-REQ-004, DOC-REQ-015).
- [X] T031 [US3] Preserve MoonMind workflow identity as the durable primary handle in `api_service/api/routers/agent_queue.py`, `api_service/api/routers/task_dashboard_view_model.py`, and `moonmind/agents/codex_worker/worker.py` (DOC-REQ-005, DOC-REQ-016).
- [X] T032 [US3] Align Jules runtime discovery, supported runtime labels, and provider-handle presentation in `moonmind/mcp/jules_tool_registry.py`, `moonmind/agents/codex_worker/cli.py`, and `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-016).

**Checkpoint**: User Story 3 delivers truthful cancellation, secret hygiene, and compatibility-safe identity surfaces.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize traceability and run repository-standard runtime validation.

- [X] T033 [P] Sync the final Jules Temporal contract and implementation mapping in `specs/048-jules-external-events/contracts/jules-temporal-activity-contract.md` and `specs/048-jules-external-events/contracts/requirements-traceability.md` (DOC-REQ-013, DOC-REQ-017).
- [X] T034 [P] Refresh spec 048 DOC-REQ coverage assertions in `tests/unit/specs/test_doc_req_traceability_048.py` (DOC-REQ-013, DOC-REQ-017).
- [X] T035 Run focused Jules runtime regression with `./tools/test_unit.sh` covering `tests/unit/jules/`, `tests/unit/workflows/adapters/test_jules_client.py`, `tests/unit/workflows/temporal/test_activity_runtime.py`, `tests/unit/api/routers/`, `tests/unit/agents/codex_worker/`, `tests/unit/mcp/test_jules_tool_registry.py`, and `tests/contract/test_temporal_activity_topology.py` (DOC-REQ-001 through DOC-REQ-016 validation sweep).
- [X] T036 Run full repository validation with `./tools/test_unit.sh` (DOC-REQ-017).
- [X] T037 Run runtime scope gates with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` (DOC-REQ-017).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No prerequisites.
- **Phase 2 (Foundational)**: Depends on Phase 1 and blocks all story work.
- **Phase 3 (US1)**: Depends on Phase 2 and delivers the MVP start path.
- **Phase 4 (US2)**: Depends on Phase 2 and can proceed after the shared Jules foundations are stable.
- **Phase 5 (US3)**: Depends on Phase 2 and benefits from the runtime surfaces delivered in US1 and US2.
- **Phase 6 (Polish)**: Depends on completion of the intended user stories.

### User Story Dependencies

- **US1 (P1)**: Starts immediately after the foundational phase and defines the MVP.
- **US2 (P1)**: Depends on the same shared Jules foundations as US1; it can begin once those are complete.
- **US3 (P2)**: Depends on the presence of start/monitoring compatibility surfaces from US1 and US2.

### Within Each User Story

- Add or update the story tests first, confirm they fail, then implement the runtime changes.
- Shared schema and normalization changes precede higher-level routing and compatibility work.
- Temporal activity changes precede worker/API compatibility refinements for the same story.
- Re-run each story's validation set before moving on.

### Parallel Opportunities

- `T002` and `T003` can run in parallel after `T001`.
- `T006` can run in parallel with `T004-T005` during the foundational phase.
- US1 test tasks `T008-T011` can run in parallel.
- US2 test tasks `T016-T019` can run in parallel.
- US3 test tasks `T025-T028` can run in parallel.
- Polish tasks `T033` and `T034` can run in parallel before the final validation commands.

---

## Parallel Example: User Story 1

```bash
Task T008: tests/unit/jules/test_jules_runtime.py
Task T009: tests/unit/api/routers/test_agent_queue.py + tests/unit/api/routers/test_mcp_tools.py + tests/unit/api/routers/test_task_dashboard_view_model.py
Task T010: tests/unit/workflows/temporal/test_activity_runtime.py + tests/contract/test_temporal_activity_topology.py
Task T011: tests/unit/agents/codex_worker/test_cli.py + tests/unit/agents/codex_worker/test_worker.py
```

## Parallel Example: User Story 2

```bash
Task T016: tests/unit/jules/test_status.py
Task T017: tests/unit/workflows/adapters/test_jules_client.py
Task T018: tests/unit/workflows/temporal/test_activity_runtime.py
Task T019: tests/unit/agents/codex_worker/test_worker.py
```

## Parallel Example: User Story 3

```bash
Task T025: tests/unit/workflows/temporal/test_activity_runtime.py + tests/contract/test_temporal_activity_topology.py
Task T026: tests/unit/api/routers/test_agent_queue.py + tests/unit/api/routers/test_task_dashboard_view_model.py
Task T027: tests/unit/agents/codex_worker/test_cli.py + tests/unit/agents/codex_worker/test_worker.py
Task T028: tests/unit/mcp/test_jules_tool_registry.py + tests/unit/api/routers/test_mcp_tools.py
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2.
2. Deliver Phase 3 (US1) start-path behavior.
3. Validate US1 independently with `T008-T011`.
4. Use that slice as the first production-ready Jules Temporal increment.

### Incremental Delivery

1. Finish Setup + Foundational to lock gate, adapter, status, queue, and artifact primitives.
2. Deliver US1 for start-path gating and stable correlation.
3. Deliver US2 for monitoring, polling fallback, and artifact-backed results.
4. Deliver US3 for truthful cancellation, secret hygiene, and compatibility-safe identity.
5. Finish with Phase 6 validation and runtime-scope gates.

### Parallel Team Strategy

1. Complete Phase 1 and Phase 2 together because they define shared Jules primitives.
2. After the foundations are stable:
   Engineer A can own US1.
   Engineer B can own US2.
   Engineer C can own US3.
3. Rejoin for the Phase 6 validation sweep and scope gates.

---

## Quality Gates

1. Runtime task gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime diff gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
3. Repository validation gate: `./tools/test_unit.sh`
4. Traceability gate: every `DOC-REQ-*` retains at least one implementation task and one validation task.
5. Runtime-mode gate: production runtime files and automated validation tasks both remain explicit in this task list.

## Task Summary

- Total tasks: **37**
- Story task count: **US1 = 8**, **US2 = 9**, **US3 = 8**
- Parallelizable tasks (`[P]`): **17**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit file paths**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T015, T023 | T011, T018, T019 |
| DOC-REQ-002 | T001, T004, T014, T020 | T010, T016 |
| DOC-REQ-003 | T002, T012, T013, T014, T032 | T008, T009, T010, T011, T028 |
| DOC-REQ-004 | T005, T021, T030 | T017, T018 |
| DOC-REQ-005 | T003, T007, T014, T015, T031 | T010, T018 |
| DOC-REQ-006 | T004, T020, T021, T023 | T016, T018, T019 |
| DOC-REQ-007 | T006, T029 | T010, T025 |
| DOC-REQ-008 | T001, T014, T024 | T010, T018 |
| DOC-REQ-009 | T001, T020, T021 | T018 |
| DOC-REQ-010 | T003, T007, T022 | T018 |
| DOC-REQ-011 | T006, T029 | T025, T027 |
| DOC-REQ-012 | T021, T023, T029 | T018, T019, T027 |
| DOC-REQ-013 | T024, T033 | T018, T034 |
| DOC-REQ-014 | T003, T007, T022 | T018, T025 |
| DOC-REQ-015 | T005, T030 | T017, T018, T028 |
| DOC-REQ-016 | T013, T015, T031, T032 | T009, T011, T026, T027 |
| DOC-REQ-017 | T012, T033 | T011, T034, T035, T036, T037 |

Coverage gate rule: each `DOC-REQ-*` must retain at least one implementation task and at least one validation task before implementation starts and before publish.
