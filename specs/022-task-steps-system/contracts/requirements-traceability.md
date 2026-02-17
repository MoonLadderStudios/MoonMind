# Requirements Traceability: 022-task-steps-system

| Source Requirement | Spec FR Mapping | Planned Implementation Surfaces | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001 | `moonmind/agents/codex_worker/worker.py` execute loop + single-claim flow | Worker unit tests verifying one job claim and sequential invocations |
| DOC-REQ-002 | FR-002 | `moonmind/workflows/agent_queue/task_contract.py`, `moonmind/agents/codex_worker/worker.py` | Task stage plan tests and worker stage event assertions |
| DOC-REQ-003 | FR-003 | `moonmind/workflows/agent_queue/task_contract.py` | Contract tests for missing/empty steps implicit behavior |
| DOC-REQ-004 | FR-004 | `moonmind/workflows/agent_queue/task_contract.py` step model validators | Contract tests for forbidden step-level fields and schema errors |
| DOC-REQ-005 | FR-005 | `moonmind/workflows/agent_queue/task_contract.py`, `moonmind/agents/codex_worker/worker.py` | Contract tests requiring task objective + worker prompt composition tests |
| DOC-REQ-006 | FR-005, FR-006 | `moonmind/agents/codex_worker/worker.py` step prompt/skill precedence | Worker tests for effective skill precedence and instruction assembly |
| DOC-REQ-007 | FR-007 | `moonmind/agents/codex_worker/worker.py` prepare skill materialization selection | Worker tests for union skill materialization inputs |
| DOC-REQ-008 | FR-006, FR-008, FR-009 | `moonmind/agents/codex_worker/worker.py` execute step loop and failure short-circuit | Worker tests for started/finished/failed events and fail-fast behavior |
| DOC-REQ-009 | FR-009, FR-010 | `moonmind/agents/codex_worker/worker.py` publish stage entry gating | Worker tests confirming publish only runs after all successful steps |
| DOC-REQ-010 | FR-011 | `moonmind/agents/codex_worker/worker.py` cancellation boundary checks + ack path | Worker tests for cancellation during/after step and no completion/failure transition |
| DOC-REQ-011 | FR-008 | `moonmind/agents/codex_worker/worker.py` event emission + step artifact paths | Worker tests for `task.steps.plan` + step event payload fields + artifact names |
| DOC-REQ-012 | FR-012 | `api_service/static/task_dashboard/dashboard.js` queue new form | Dashboard unit/static assertions for steps payload emission and publish default |
| DOC-REQ-013 | FR-013 | `moonmind/workflows/agent_queue/task_contract.py` capability derivation | Contract tests covering task + step skill required capability unions |
| DOC-REQ-014 | FR-014 | `moonmind/workflows/agent_queue/task_contract.py`, `moonmind/agents/codex_worker/worker.py` | Contract/worker tests verifying deterministic rejection of steps+container |
| DOC-REQ-015 | FR-015 | Runtime + tests across contract, worker, and dashboard files | `./tools/test_unit.sh` targeted suites and full regression run |
