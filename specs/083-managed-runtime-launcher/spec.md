# Feature Specification: Implement ManagedRuntimeLauncher to be fully functional

**Feature Branch**: `083-managed-runtime-launcher`  
**Created**: 2026-03-17
**Status**: Draft  

## User Scenarios & Testing *(mandatory)*

### User Story 1 - System orchestrates managed agents via launcher (Priority: P1)

The system should be able to run managed agent processes in the background using `ManagedRuntimeLauncher` within the Temporal `agent_runtime` activity, instead of being stubbed in the workflow worker.

**Why this priority**: Required for production execution of managed agents. Without this, managed agents fail due to a 1-hour timeout while stuck in the "launching" state.

**Independent Test**: Can be tested by running unit tests for the agent runtime activities and workflow adapters.

**Acceptance Scenarios**:

1. **Given** a valid agent run request, **When** the workflow executes the `agent_runtime.launch` activity, **Then** the `ManagedRuntimeLauncher` spawns the process and the `ManagedRunSupervisor` monitors it asynchronously.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide an `agent_runtime.launch` activity in `TemporalAgentRuntimeActivities`.
- **FR-002**: System MUST inject `ManagedRuntimeLauncher` into the activity dependencies via `worker_runtime.py`.
- **FR-003**: System MUST update `ManagedAgentAdapter` to use a `run_launcher` callback to initiate the run.
- **FR-004**: System MUST trigger the `ManagedRunSupervisor.supervise()` method via a background `asyncio` task within the activity worker.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests pass and correctly inject `run_launcher` dependencies.
- **SC-002**: Workflows launching managed agents no longer hang indefinitely in the "launching" state.
