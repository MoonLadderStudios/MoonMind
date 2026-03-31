# Requirements Traceability

| Source Requirement | Functional Requirement | Implementation Surface | Validation Strategy |
| ------------------ | ---------------------- | ---------------------- | ------------------- |
| **DOC-REQ-001** | FR-001 | `AgentRunStatus`, `AgentRunHandle`, `AgentRunResult` classes | Unit tests for boundary enforcement, ensuring non-canonical structures raise errors or drop invalid fields. |
| **DOC-REQ-002** | FR-002, FR-003 | `build_canonical_start_handle`, `build_canonical_status`, `build_canonical_result`, `UnsupportedStatusError` | Positive and negative tests supplying valid and invalid states/metrics. |
| **DOC-REQ-003** | FR-004 | Activity boundary decorators / helpers in `agent_runtime_models.py` | Unit tests to ensure `apply_external_provider_status` behaves uniquely vs managed when needed. |
| **DOC-REQ-004** | FR-005 | `build_canonical_status`, nested `metadata` field validators | Supply inputs with raw `providerStatus` etc, assert they nest correctly under dict. |
| **DOC-REQ-005** | FR-001, FR-005 | `AgentRunStatus`, `build_canonical_status` | Ensure exceptions are raised if top-level fields like `provider_status` are passed. |
| **DOC-REQ-006** | FR-006 | `tests/unit/schemas/test_agent_runtime_models_boundary.py` | Pytest fixtures confirming proper failures without ever entering workflow layers. |
