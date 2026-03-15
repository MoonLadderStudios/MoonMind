# Feature Specification: Implement Phase 3 Agent Adapters

**Feature Branch**: `001-agent-execution-phase3`
**Created**: 2024-05-14
**Status**: Draft
**Input**: "Implement phase 3 of docs/Temporal/ManagedAndExternalAgentExecutionModel.md"

## Requirements (extracted from Phase 3)

### Source Requirements
- **DOC-REQ-EXT-ADAPT**: Implement `ExternalAgentAdapter` implementing `AgentAdapter` interface.
- **DOC-REQ-EXT-RESP**: External adapter MUST translate `AgentExecutionRequest` to provider payload, exchange artifacts, pass callbacks, normalize `AgentRunStatus`, fetch outputs/diagnostics, and cancel remote work.
- **DOC-REQ-MNG-ADAPT**: Implement `ManagedAgentAdapter` implementing `AgentAdapter` interface.
- **DOC-REQ-MNG-RESP**: Managed adapter MUST resolve auth/runtime profiles, prepare local workspace context, launch asynchronously, normalize `AgentRunStatus`, fetch outputs/logs, and cancel runs. (Can use placeholder/mock delegates for actual runtime launch if not yet built in Phase 4).
- **DOC-REQ-LIFECYCLE**: Both MUST conform to the same lifecycle contract and state model (`start`, `status`, `fetch_result`, `cancel`).
- **DOC-REQ-POLLING**: Adapter implementations MUST support bounded status polling and durable callback/event resumption.

### Success Criteria
- **SC-001**: `ExternalAgentAdapter` exists and fulfills `AgentAdapter` protocol.
- **SC-002**: `ManagedAgentAdapter` exists and fulfills `AgentAdapter` protocol.
- **SC-003**: Both classes have unit validation tests demonstrating `start`, `status`, `fetch_result`, and `cancel`.
