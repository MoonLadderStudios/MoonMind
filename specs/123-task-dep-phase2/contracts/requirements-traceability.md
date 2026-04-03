# Requirements Traceability: Task Dependencies Phase 2 - MoonMind.Run Dependency Gate

| Requirement | Source Requirement | Planned Implementation Surface | Validation Strategy |
|-------------|--------------------|-------------------------------|--------------------|
| FR-001 | DOC-REQ-001 | `moonmind/workflows/temporal/workflows/run.py` pre-planning dependency parsing | Workflow-boundary test proves dependency parsing occurs before planning |
| FR-002, FR-003 | DOC-REQ-002 | `moonmind/workflows/temporal/workflows/run.py` waiting state transition and memo metadata | Workflow-boundary + direct unit tests assert `waiting_on_dependencies`, `dependency_wait`, and memo dependency IDs |
| FR-004 | DOC-REQ-003 | `moonmind/workflows/temporal/workflows/run.py` external handle wait helper | Workflow-boundary test asserts planning runs only after dependency handles resolve |
| FR-005 | DOC-REQ-004 | `moonmind/workflows/temporal/workflows/run.py` interruptible dependency wait | Workflow-boundary test cancels dependent run during wait and asserts the wait is interrupted cleanly |
| FR-006, FR-010 | DOC-REQ-005 | `moonmind/workflows/temporal/workflows/run.py` patched/unpatched branch and `tests/unit/workflows/temporal/workflows/test_run_scheduling.py` | Compatibility test covers both patched and unpatched paths |
| FR-007 | DOC-REQ-006 | `moonmind/workflows/temporal/workflows/run.py` legacy direct-to-planning path | Workflow-boundary test with empty dependencies confirms direct planning path |
| FR-008, FR-010 | DOC-REQ-007 | `moonmind/workflows/temporal/workflows/run.py` dependency-specific failure handling and `tests/unit/workflows/temporal/workflows/test_run_signals_updates.py` | Direct unit test simulates failed prerequisite and `./tools/test_unit.sh` validates degraded outcomes |
| FR-005 | DOC-REQ-008 | `moonmind/workflows/temporal/workflows/run.py` cancel-safe dependency wait path | Workflow-boundary test confirms dependent-run cancellation does not mutate prerequisite workflows |
| FR-009 | DOC-REQ-009 | `moonmind/workflows/temporal/workflows/run.py` post-wait pause gate | Workflow-boundary test confirms pause gate is honored after dependencies resolve |
