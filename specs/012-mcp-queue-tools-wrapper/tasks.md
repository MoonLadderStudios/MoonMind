# Tasks: Agent Queue MCP Tools Wrapper (Milestone 4)

**Input**: Design documents from `/specs/012-mcp-queue-tools-wrapper/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare Milestone 4 scaffolding for MCP router/registry and tests.

- [X] T001 Verify branch `012-mcp-queue-tools-wrapper` and feature artifacts exist in `specs/012-mcp-queue-tools-wrapper/`.
- [X] T002 Create MCP package scaffolding in `moonmind/mcp/` and test directories in `tests/unit/mcp/`.
- [X] T003 [P] Create router test scaffolding in `tests/unit/api/routers/test_mcp_tools.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared MCP contracts and router wiring required by all stories.

- [X] T004 Add MCP tool registry core structures and envelope models in `moonmind/mcp/tool_registry.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-007).
- [X] T005 Register queue tool definition metadata and JSON argument schemas in `moonmind/mcp/tool_registry.py` (DOC-REQ-002, DOC-REQ-005).
- [X] T006 Add MCP router endpoints (`GET /mcp/tools`, `POST /mcp/tools/call`) in `api_service/api/routers/mcp_tools.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-006, DOC-REQ-007).
- [X] T007 Wire MCP router into API startup in `api_service/main.py` (DOC-REQ-001, DOC-REQ-004).
- [X] T008 [P] Add module exports for MCP registry package in `moonmind/mcp/__init__.py` (DOC-REQ-007).

**Checkpoint**: MCP wrapper endpoints are wired and tool metadata contracts exist.

---

## Phase 3: User Story 1 - Discover Queue Tools (Priority: P1) 🎯 MVP

**Goal**: Agents can list available queue tools with stable schemas.

**Independent Test**: `GET /mcp/tools` returns queue tools, descriptions, and argument schemas.

### Tests for User Story 1

- [X] T009 [P] [US1] Add API tests for `/mcp/tools` discovery payload and schema shape in `tests/unit/api/routers/test_mcp_tools.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005, DOC-REQ-007).
- [X] T010 [P] [US1] Add registry tests for deterministic tool ordering/naming in `tests/unit/mcp/test_tool_registry.py` (DOC-REQ-002, DOC-REQ-005).

### Implementation for User Story 1

- [X] T011 [US1] Implement queue tool definitions (`queue.enqueue`, `queue.claim`, `queue.heartbeat`, `queue.complete`, `queue.fail`, `queue.get`, `queue.list`) in `moonmind/mcp/tool_registry.py` (DOC-REQ-002, DOC-REQ-005).
- [X] T012 [US1] Implement `/mcp/tools` route using registry discovery output in `api_service/api/routers/mcp_tools.py` (DOC-REQ-004, DOC-REQ-005).

**Checkpoint**: Tool discovery endpoint is complete and independently testable.

---

## Phase 4: User Story 2 - Call Queue Tools with Service Parity (Priority: P1)

**Goal**: MCP tool calls execute via the same queue service methods as REST and return REST-equivalent results.

**Independent Test**: `POST /mcp/tools/call` successfully dispatches queue operations and preserves response shapes.

### Tests for User Story 2

- [X] T013 [P] [US2] Add registry dispatch tests asserting service-method invocation parity in `tests/unit/mcp/test_tool_registry.py` (DOC-REQ-003, DOC-REQ-008).
- [X] T014 [P] [US2] Add API tests for tool call success, unknown tool, and invalid arguments in `tests/unit/api/routers/test_mcp_tools.py` (DOC-REQ-001, DOC-REQ-004, DOC-REQ-006, DOC-REQ-007).
- [X] T015 [P] [US2] Add response-shape tests verifying MCP queue results match REST model fields in `tests/unit/mcp/test_tool_registry.py` and `tests/unit/api/routers/test_mcp_tools.py` (DOC-REQ-009).

### Implementation for User Story 2

- [X] T016 [US2] Implement tool dispatch execution context and argument validation in `moonmind/mcp/tool_registry.py` (DOC-REQ-003, DOC-REQ-006).
- [X] T017 [US2] Implement queue service-backed handlers for enqueue/claim/heartbeat/complete/fail/get/list in `moonmind/mcp/tool_registry.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-008, DOC-REQ-009).
- [X] T018 [US2] Implement MCP router error mapping for tool and queue exceptions in `api_service/api/routers/mcp_tools.py` (DOC-REQ-006, DOC-REQ-009).

**Checkpoint**: Tool call wrapper behavior is feature-complete and independently testable.

---

## Phase 5: User Story 3 - Codex Tool Adapter Guidance (Priority: P2)

**Goal**: Operators can configure Codex to call MoonMind tool endpoints.

**Independent Test**: Follow docs to list tools and perform one queue tool call.

### Tests for User Story 3

- [X] T019 [P] [US3] Add registry/router tests for optional `queue.upload_artifact` argument validation and invalid base64 handling in `tests/unit/mcp/test_tool_registry.py` (DOC-REQ-002, DOC-REQ-006).
- [X] T020 [P] [US3] Add router tests for `queue.get` not-found mapping and typed tool errors in `tests/unit/api/routers/test_mcp_tools.py` (DOC-REQ-006, DOC-REQ-009).

### Implementation for User Story 3

- [X] T021 [US3] Implement optional `queue.upload_artifact` tool handler with base64 decoding in `moonmind/mcp/tool_registry.py` (DOC-REQ-002, DOC-REQ-006).
- [X] T022 [US3] Add concise Codex HTTP adapter setup doc in `docs/CodexMcpToolsAdapter.md` (DOC-REQ-010).

**Checkpoint**: Codex integration guidance and optional upload tool support are complete.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final traceability reconciliation and validation command execution.

- [X] T023 [P] Reconcile implementation with `specs/012-mcp-queue-tools-wrapper/contracts/requirements-traceability.md` and update drift.
- [X] T024 [P] Update quickstart/docs examples for final tool names and payload fields in `specs/012-mcp-queue-tools-wrapper/quickstart.md` and `docs/CodexMcpToolsAdapter.md` (DOC-REQ-010).
- [ ] T025 Run unit validation via `./tools/test_unit.sh` including new MCP router/registry tests.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phase 3/4/5 -> Phase 6.
- All user stories depend on foundational tasks T004-T008.
- Polish phase runs after selected user stories are complete.

### User Story Dependencies

- US1 starts after foundational setup.
- US2 depends on US1 registry/router discovery artifacts.
- US3 depends on US2 tool-call execution path.

### Parallel Opportunities

- T003, T008 can run in parallel during setup/foundation.
- T009/T010, T013/T014/T015, and T019/T020 are parallelizable test tasks.
- T023/T024 can run in parallel during polish.

---

## Implementation Strategy

### MVP First (US1)

1. Deliver tool discovery endpoint and stable queue tool definitions.
2. Validate schema contract with API and registry tests.
3. Expand to call-dispatch and docs polish.

### Incremental Delivery

1. Implement shared-service tool execution and response parity (US2).
2. Add optional upload tool + Codex adapter guide (US3).
3. Execute full unit validation and close traceability.

### Runtime Scope Commitments

- Production runtime files will be modified in `api_service/` and `moonmind/mcp/`.
- Validation coverage will be delivered with new unit tests plus execution of `./tools/test_unit.sh`.
