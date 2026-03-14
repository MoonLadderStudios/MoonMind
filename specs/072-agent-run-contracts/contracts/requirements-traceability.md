# Requirements Traceability: Agent Runtime Phase 1 Contracts

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §11 Phase 1 | FR-001 | `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/adapters/agent_adapter.py`, `moonmind/workflows/adapters/jules_agent_adapter.py` | Unit tests verify required contract classes and adapter interface are present and importable. |
| DOC-REQ-002 | §3 `AgentExecutionRequest` | FR-002 | `moonmind/schemas/agent_runtime_models.py` | Unit tests validate required request fields, aliases, and invalid payload rejection. |
| DOC-REQ-003 | §3 `AgentRunHandle` | FR-003 | `moonmind/schemas/agent_runtime_models.py` | Unit tests validate handle fields and canonical status compatibility. |
| DOC-REQ-004 | §3 `AgentRunStatus` | FR-004 | `moonmind/schemas/agent_runtime_models.py` | Unit tests validate allowed states and terminal-state helper semantics. |
| DOC-REQ-005 | §3 `AgentRunResult` | FR-005 | `moonmind/schemas/agent_runtime_models.py` | Unit tests validate result model shape and reference-oriented output contract. |
| DOC-REQ-006 | §3 Idempotency Requirements | FR-006 | `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/adapters/jules_agent_adapter.py` | Unit tests validate non-empty idempotency requirements and repeated-start idempotency behavior. |
| DOC-REQ-007 | §4 `AgentAdapter` | FR-007, FR-008 | `moonmind/workflows/adapters/agent_adapter.py`, `moonmind/workflows/adapters/jules_agent_adapter.py` | Adapter tests validate shared interface method coverage and normalized return types. |
| DOC-REQ-008 | §7 `ManagedAgentAuthProfile` and secret handling rules | FR-009, FR-011 | `moonmind/schemas/agent_runtime_models.py` | Unit tests validate auth profile fields and ensure raw credential fields are not accepted. |
| DOC-REQ-009 | §7 per-profile concurrency/cooldown | FR-009 | `moonmind/schemas/agent_runtime_models.py` | Unit tests validate per-profile concurrency/cooldown constraints and reject invalid values. |
| DOC-REQ-010 | §8 artifact/log discipline | FR-010 | `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/adapters/jules_agent_adapter.py` | Unit tests validate reference-based outputs and reject oversized inline payload misuse. |
| DOC-REQ-011 | Runtime scope guard (user request) | FR-001 | `moonmind/schemas/agent_runtime_models.py`, `moonmind/workflows/adapters/*.py`, `tests/unit/schemas/test_agent_runtime_models.py`, `tests/unit/workflows/adapters/test_jules_agent_adapter.py` | `./tools/test_unit.sh` must pass with new production runtime code and related tests. |

## Coverage Gate

- Total source requirements: **11** (`DOC-REQ-001` through `DOC-REQ-011`).
- Every source requirement has mapped FR(s), planned runtime implementation surfaces, and validation strategy.
- Planning is invalid if any `DOC-REQ-*` row loses mapping or validation coverage.
