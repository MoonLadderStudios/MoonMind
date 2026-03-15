# Implementation Tasks

## Phase 3 Agent Adapters

### 1. ExternalAgentAdapter
- [X] T01 Create `api_service/services/temporal/adapters/external.py` with `ExternalAgentAdapter` implementing `start`, `status`, `fetch_result`, `cancel`. (DOC-REQ-EXT-ADAPT, DOC-REQ-LIFECYCLE)
- [X] T02 Implement mock/simulated translation of `AgentExecutionRequest` in `ExternalAgentAdapter`. (DOC-REQ-EXT-RESP, DOC-REQ-POLLING)

### 2. ManagedAgentAdapter
- [X] T03 Create `api_service/services/temporal/adapters/managed.py` with `ManagedAgentAdapter` implementing `start`, `status`, `fetch_result`, `cancel`. (DOC-REQ-MNG-ADAPT, DOC-REQ-LIFECYCLE)
- [X] T04 Implement mock/simulated workspace prep and launch in `ManagedAgentAdapter`. (DOC-REQ-MNG-RESP, DOC-REQ-POLLING)

### 3. Validation Tests
- [X] T05 Write unit tests for `ExternalAgentAdapter` in `tests/temporal/test_external_adapter.py`. (DOC-REQ-EXT-ADAPT, DOC-REQ-EXT-RESP, DOC-REQ-LIFECYCLE)
- [X] T06 Write unit tests for `ManagedAgentAdapter` in `tests/temporal/test_managed_adapter.py`. (DOC-REQ-MNG-ADAPT, DOC-REQ-MNG-RESP, DOC-REQ-LIFECYCLE)
