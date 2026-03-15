# Requirements Checklist

- [ ] DOC-REQ-EXT-ADAPT: Implement `ExternalAgentAdapter` implementing `AgentAdapter` interface.
- [ ] DOC-REQ-EXT-RESP: External adapter translate `AgentExecutionRequest` to provider payload, exchange artifacts, pass callbacks, normalize `AgentRunStatus`, fetch outputs/diagnostics, cancel remote work.
- [ ] DOC-REQ-MNG-ADAPT: Implement `ManagedAgentAdapter` implementing `AgentAdapter` interface.
- [ ] DOC-REQ-MNG-RESP: Managed adapter resolve auth/runtime profiles, prepare local workspace context, launch asynchronously, normalize `AgentRunStatus`, fetch outputs/logs, cancel runs.
- [ ] DOC-REQ-LIFECYCLE: Conform to the same lifecycle contract and state model.
- [ ] DOC-REQ-POLLING: Support bounded status polling and durable callback/event resumption.
