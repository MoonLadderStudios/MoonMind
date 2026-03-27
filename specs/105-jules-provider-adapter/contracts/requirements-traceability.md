# Requirements Traceability: Jules Provider Adapter Runtime Alignment

| DOC-REQ | Functional Requirement(s) | Implementation Surface | Validation Strategy |
|---|---|---|---|
| DOC-REQ-001 | FR-005, FR-011 | `jules_client.py`, `jules_agent_adapter.py`, workflow helper changes avoid moving orchestration into transport | Unit tests confirm transport helpers stay transport-focused; workflow tests cover orchestration separately |
| DOC-REQ-002 | FR-005, FR-011 | `jules_agent_adapter.py`, `agent_run.py` | Existing adapter tests plus updated workflow-boundary tests |
| DOC-REQ-003 | FR-006, FR-011 | `jules_agent_adapter.py` start behavior, external/integration start paths | Unit test for AUTO_CREATE_PR start behavior |
| DOC-REQ-004 | FR-007, FR-011 | `run.py`, `jules_activities.py`, merge helper path | Workflow test for successful branch publication including merge |
| DOC-REQ-005 | FR-007, FR-008, FR-009 | `run.py`, result handling, merge/base-update failure mapping | Workflow tests for missing PR URL, base-update failure, merge rejection, verification failure |
| DOC-REQ-006 | FR-001, FR-012 | `run.py`, `worker_runtime.py` | Workflow tests show consecutive Jules work executes as one bundle / one provider session |
| DOC-REQ-007 | FR-001, FR-002, FR-012 | Bundle compiler/helper logic plus `run.py` dispatch | Workflow tests inspect compiled one-shot brief shape |
| DOC-REQ-008 | FR-003, FR-009, FR-012 | Bundle manifest/result metadata handling in `run.py` / `agent_run.py` | Workflow tests validate bundle IDs, node IDs, and manifest refs are preserved |
| DOC-REQ-009 | FR-004, FR-010, FR-012 | `agent_run.py`, `jules_activities.py`, removal of normal `jules_session_id` chaining | Workflow tests verify no normal step chaining via `send_message`; clarification path tests remain passing |
| DOC-REQ-010 | FR-009 | Bundle result summary handling in `agent_run.py` / `run.py` | Workflow tests assert incomplete checklist state is surfaced, not hidden |
| DOC-REQ-011 | FR-008, FR-009, FR-012 | MoonMind verification and publish-result ownership in `run.py` / result handling | Workflow tests validate provider success can still end as MoonMind non-success |
| DOC-REQ-012 | FR-004, FR-005, FR-010 | `agent_run.py`, `run.py`, Jules activities | Workflow tests prove orchestration stays in workflow layers while transport remains thin |
