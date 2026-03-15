# Implementation Plan: Phase 3 Agent Adapters

## Scope
Implement `ExternalAgentAdapter` and `ManagedAgentAdapter` according to Phase 3 requirements. 

## Strategy
1. **Core Interfaces**: Ensure `AgentAdapter` interface exists or create it if missing, providing `start`, `status`, `fetch_result`, `cancel`.
2. **ExternalAgentAdapter**: Create `api_service/services/temporal/adapters/external.py`. Implement methods translating `AgentExecutionRequest` to simulated external calls, pass callbacks, fetch results.
3. **ManagedAgentAdapter**: Create `api_service/services/temporal/adapters/managed.py`. Implement methods to resolve profiles and launch asynchronously (can be a mock launcher for now).
4. **Validation**: Write tests for both adapters in `tests/test_adapters.py` or similar to prove lifecycle compliance.

## Steps
1. Create/update the `AgentAdapter` base class and shared data models.
2. Implement `ExternalAgentAdapter`.
3. Implement `ManagedAgentAdapter`.
4. Add unit validation tests.
