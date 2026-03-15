# Requirements Traceability

| Requirement ID | Description | Validation Strategy |
| --- | --- | --- |
| DOC-REQ-EXT-ADAPT | Implement `ExternalAgentAdapter` implementing `AgentAdapter` interface. | Unit test verifying inheritance and instantiation. |
| DOC-REQ-EXT-RESP | External adapter translate `AgentExecutionRequest` to provider payload, exchange artifacts, pass callbacks, normalize `AgentRunStatus`, fetch outputs/diagnostics, cancel remote work. | Unit tests for `ExternalAgentAdapter` methods simulating correct response normalization. |
| DOC-REQ-MNG-ADAPT | Implement `ManagedAgentAdapter` implementing `AgentAdapter` interface. | Unit test verifying inheritance and instantiation. |
| DOC-REQ-MNG-RESP | Managed adapter resolve auth/runtime profiles, prepare local workspace context, launch asynchronously, normalize `AgentRunStatus`, fetch outputs/logs, cancel runs. | Unit tests for `ManagedAgentAdapter` methods simulating correct response normalization. |
| DOC-REQ-LIFECYCLE | Conform to the same lifecycle contract and state model. | Interface check ensuring both have `start`, `status`, `fetch_result`, `cancel`. |
| DOC-REQ-POLLING | Support bounded status polling and durable callback/event resumption. | Ensure methods return models supporting status enum and polling data. |
