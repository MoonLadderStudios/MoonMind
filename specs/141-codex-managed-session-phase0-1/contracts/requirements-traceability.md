# Requirements Traceability: Codex Managed Session Phase 0 and Phase 1

## Source Requirement Mapping

| Source ID | Functional Requirements | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `docs/ManagedAgents/CodexManagedSessionPlane.md` truth-surface sections; `ManagedSessionStore` references remain recovery-oriented | Manual doc review plus spec checklist confirming operator/audit truth, recovery index, and disposable cache are distinct |
| DOC-REQ-002 | FR-004, FR-005, FR-006, FR-007, FR-008 | `moonmind/workflows/temporal/workflows/agent_session.py`; `moonmind/workflows/temporal/workflows/run.py`; `moonmind/workflows/temporal/workflows/agent_run.py`; `moonmind/workflows/adapters/codex_session_adapter.py`; API/service callers | Unit tests for typed Update handlers, validators, caller routing, and absence of the generic mutating `control_action` signal |
| DOC-REQ-003 | FR-006, FR-009 | `ClearSession` request schema, workflow validator, runtime clear activity request, workflow snapshot update | Unit tests for stale epoch rejection, clear-while-clearing rejection, epoch advancement, new thread identity, and cleared active turn |
| DOC-REQ-004 | FR-002, FR-009 | Managed-session doc publication language; continuity refresh through summary/checkpoint/control/reset refs in workflow update handlers | Unit tests for update results carrying continuity refs and manual doc review of artifact-backed evidence requirements |
| DOC-REQ-005 | FR-002 | `docs/ManagedAgents/CodexManagedSessionPlane.md` artifact expectations section | Manual doc review confirming controller/supervisor are the only production publishers and in-container helpers are fallback/diagnostic only |

## Gate Result

All `DOC-REQ-*` source requirements in `spec.md` map to at least one functional requirement and have planned implementation and validation coverage.
