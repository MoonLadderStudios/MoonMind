# Task Execution Plan: MoonMind.AgentRun Workflow

## Strategy

- **MVP**: Complete User Story 1 (Managed Agent Execution).
- **Incremental Delivery**: Complete User Story 2 (Cancellation Cleanup) immediately after MVP.

## Phase 1: Setup

- [X] T001 Create `api_service/services/temporal/workflows/agent_run.py`
- [X] T002 Create `api_service/services/temporal/adapters/` directory and `__init__.py`
- [X] T003 Create `tests/services/temporal/workflows/test_agent_run.py`

## Phase 2: Foundational

- [X] T004 Define `AgentExecutionRequest`, `AgentRunResult`, `AgentRunStatus`, and `AgentRunHandle` models in `api_service/services/temporal/workflows/shared.py` (covers DOC-REQ-006)
- [X] T005 Define `AgentAdapter` base class interface in `api_service/services/temporal/adapters/base.py` with `start`, `status`, `fetch_result`, and `cancel` methods (covers DOC-REQ-002)

## Phase 3: User Story 1 - Managed Agent Execution

**Goal**: Run a managed agent securely and asynchronously and wait for completion events.
**Independent Test**: Mock the adapter and verify the workflow completes successfully.

- [X] T006 [US1] Implement `ManagedAgentAdapter` stub in `api_service/services/temporal/adapters/managed.py` for routing.
- [X] T007 [US1] Implement `ExternalAgentAdapter` stub in `api_service/services/temporal/adapters/external.py` for routing.
- [X] T008 [US1] Implement `MoonMindAgentRun` Temporal workflow in `api_service/services/temporal/workflows/agent_run.py` (covers DOC-REQ-001)
- [X] T009 [US1] Add adapter routing logic to `MoonMindAgentRun` to call `start` based on `agent_kind` (covers DOC-REQ-002 implementation)
- [X] T010 [US1] Implement wait phase loop in `MoonMindAgentRun` using `asyncio.Event` and Temporal signals/updates (covers DOC-REQ-003)
- [X] T011 [US1] Implement timeout and intervention handling in wait loop (covers DOC-REQ-004)
- [X] T012 [US1] Add post-run artifact publishing logic to `MoonMindAgentRun` (covers DOC-REQ-005)
- [X] T013 [US1] Return normalized `AgentRunResult` from `MoonMindAgentRun` (covers DOC-REQ-006 implementation)
- [X] T014 [US1] Write unit tests in `tests/services/temporal/workflows/test_agent_run.py` validating basic workflow execution with a mocked adapter (covers DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-006 validation)

## Phase 4: User Story 2 - Cancellation Cleanup

**Goal**: Invoke specific cancellation cleanup against the active adapter when parent is cancelled.
**Independent Test**: Cancel workflow in-flight and verify adapter cancellation.

- [X] T015 [US2] Wrap wait phase in `MoonMindAgentRun` in `try...except asyncio.CancelledError` block.
- [X] T016 [US2] Inside exception handler, use Temporal's `in_background()` and `Shield()` to create a non-cancellable scope to call adapter's `cancel` (covers DOC-REQ-007)
- [X] T017 [US2] Write unit tests in `tests/services/temporal/workflows/test_agent_run.py` to trigger workflow cancellation and assert adapter `cancel` is called precisely once (covers DOC-REQ-007 validation)

## Phase 5: Polish & Cross-Cutting Concerns

- [X] T018 Run `pytest tests/services/temporal/workflows/test_agent_run.py` to ensure all execution paths are verified.
