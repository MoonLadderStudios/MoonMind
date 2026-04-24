# Requirements Traceability

| Requirement ID | Source Document | Implemented In | Validation Strategy |
| -------------- | --------------- | -------------- | ------------------- |
| **DOC-REQ-001** | `spec.md (Input)` | `moonmind/workflows/tasks/task_contract.py` | Unit tests (`test_task_contract.py`) asserting validity of `task.skills`. |
| **DOC-REQ-002** | `spec.md (Input)` | `moonmind/workflows/skills/tool_plan_contracts.py` | Unit tests (`test_tool_plan_contracts.py`) validating `step.skills` payloads logic. |
| **DOC-REQ-003** | `spec.md (Input)` | `task_contract.py` and plan models | Inheritance testing logic executed through step payload assignments. |
| **DOC-REQ-004** | `spec.md (Input)` | `task_contract.py` / Schema | Edge-case verification injected into standard schema logic testing. |
| **DOC-REQ-005** | `spec.md (Input)` | `moonmind/workflows/temporal/activity_runtime.py` | Import tracking mapping to defined Temporal routing IDs. |
| **DOC-REQ-006** | `spec.md (Input)` | `moonmind/workflows/temporal/activity_runtime.py` | Queue configuration targeting verification for `mm.activity.agent_runtime`. |
| **DOC-REQ-007** | `spec.md (Input)` | `moonmind/workflows/temporal/workflows/agent_run.py` | End-to-end integration boundaries simulating active graph traversals. |
| **DOC-REQ-008** | `spec.md (Input)` | `moonmind/workflows/temporal/workflows/agent_run.py` | Structural validation on `MoonMind.AgentRun`. |
| **DOC-REQ-009** | `spec.md (Input)` | `moonmind/workflows/temporal/workflows/agent_run.py` | Logging structure payload size validation during Temporal recording strings. |
| **DOC-REQ-010** | `spec.md (Input)` | `moonmind/workflows/temporal/workflows/agent_run.py` | Repeated rerun trigger asserting snapshot stability over deterministic runs. |
| **DOC-REQ-011** | `spec.md (Input)` | `tests/...` | The existence of the executed test suite yielding successfully. |
