# Feature Specification: MoonMind.AgentRun Workflow

**Feature Branch**: `073-agent-run-workflow`  
**Created**: 2026-03-14  
**Status**: Draft  
**Input**: Implement Phase 2 from docs/Temporal/ManagedAndExternalAgentExecutionModel.md

## Source Document Requirements

- **DOC-REQ-001**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Paragraph 1). **Summary**: Create `MoonMind.AgentRun` as the child workflow used for all true agent-runtime execution.
- **DOC-REQ-002**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Responsibilities list). **Summary**: Call the correct agent adapter based on the runtime execution request.
- **DOC-REQ-003**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Responsibilities list). **Summary**: Wait for runtime events durably using mechanisms like Signals, Updates, and Timers.
- **DOC-REQ-004**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Responsibilities list). **Summary**: Manage timeout, cancellation, and intervention logic safely.
- **DOC-REQ-005**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Responsibilities list). **Summary**: Publish outputs back to artifact storage after successful runs.
- **DOC-REQ-006**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Responsibilities list). **Summary**: Return normalized execution results to the parent `MoonMind.Run` workflow.
- **DOC-REQ-007**: **Source**: Phase 2 — Add `MoonMind.AgentRun` (Final paragraph). **Summary**: Include explicit cancellation handling so the workflow can invoke adapter/runtime cancellation during teardown using the SDK-appropriate non-cancellable cleanup path.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Managed Agent Execution (Priority: P1)

The system should run a MoonMind-managed agent securely and asynchronously, using the managed agent adapter, and correctly map its final result to the parent workflow's output.

**Why this priority**: Required for basic integration with tools like the Gemini CLI or Codex CLI.

**Independent Test**: Can be tested independently by mocking the ManagedAgentAdapter and triggering a MoonMind.AgentRun workflow run.

**Acceptance Scenarios**:

1. **Given** an execution request for a managed agent, **When** `MoonMind.AgentRun` starts, **Then** it delegates to the managed adapter and waits for completion events.
2. **Given** the agent run completes successfully, **When** the adapter returns outputs, **Then** the outputs are published and a normalized result is returned to `MoonMind.Run`.

---

### User Story 2 - Cancellation Cleanup (Priority: P1)

When a parent workflow is cancelled, `MoonMind.AgentRun` must invoke specific cancellation cleanup against the active adapter.

**Why this priority**: Prevents runaway billing and orphaned agent processes.

**Independent Test**: Can be tested independently by starting `MoonMind.AgentRun` and sending a cancellation signal to observe cleanup.

**Acceptance Scenarios**:

1. **Given** an active `MoonMind.AgentRun` workflow, **When** the workflow is cancelled, **Then** it invokes the adapter's cancel logic in a non-cancellable scope before terminating.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `MoonMind.AgentRun` workflow (satisfies DOC-REQ-001).
- **FR-002**: System MUST route execution requests to the correct adapter (satisfies DOC-REQ-002).
- **FR-003**: System MUST provide durable waiting on runtime events (satisfies DOC-REQ-003).
- **FR-004**: System MUST handle intervention requests and timeouts seamlessly (satisfies DOC-REQ-004).
- **FR-005**: System MUST publish output artifacts using the provided ref structure upon completion (satisfies DOC-REQ-005).
- **FR-006**: System MUST return normalized structured results to `MoonMind.Run` (satisfies DOC-REQ-006).
- **FR-007**: System MUST use a Temporal non-cancellable scope to invoke adapter cancellation during workflow cancellation (satisfies DOC-REQ-007).
- **FR-008**: System MUST deliver both production runtime code changes for `MoonMind.AgentRun` and its validation tests as part of this feature.

### Key Entities

- **AgentExecutionRequest**: Input structure passed to `MoonMind.AgentRun`.
- **AgentRunResult**: Output structure returned by `MoonMind.AgentRun`.
- **AgentAdapter**: The interface handling standard actions (start, fetch, cancel).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of defined DOC-REQs are mapped to functional requirements and validated via unit/integration tests.
- **SC-002**: A mock adapter run completes successfully end-to-end within the Temporal test framework.
- **SC-003**: Cancellation integration tests show adapter cancellation is invoked exactly once during workflow teardown.
