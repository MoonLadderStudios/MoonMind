# Requirements Traceability: Task Dependencies Phase 1 — Submit Contract And Validation

| FR | DOC-REQ | Implementation File | Validation Evidence |
|----|---------|---------------------|---------------------|
| FR-001 | DOC-REQ-001 | executions.py L758-763 | test_create_task_shaped_execution_prefers_task_depends_on |
| FR-002 | DOC-REQ-002 | executions.py L579-594 `_coerce_string_list` | test_create_task_shaped_execution_rejects_more_than_10_dependencies (same helper validates array of strings) |
| FR-003, FR-004 | DOC-REQ-003 | executions.py L579-594, L770 | test_create_task_shaped_execution_dedupes_and_normalizes_dependencies |
| FR-005 | DOC-REQ-004 | executions.py L772-773; service.py L220-221 | test_create_task_shaped_execution_rejects_more_than_10_dependencies; test_create_execution_rejects_more_than_10_dependencies |
| FR-006 | DOC-REQ-005 | service.py L246-248 | test_create_execution_rejects_missing_dependency |
| FR-007 | DOC-REQ-006 | service.py L250-253 | test_create_execution_rejects_non_run_dependency |
| FR-008 | DOC-REQ-007 | service.py L223-224 | test_create_execution_rejects_self_dependency |
| FR-009, FR-010 | DOC-REQ-008 | service.py L226-265 | test_create_execution_rejects_dependency_graph_too_deep, test_create_execution_rejects_dependency_graph_too_large |
| FR-011 | DOC-REQ-009 | executions.py L802-803, 856-857 | test_create_task_shaped_execution_dedupes_and_normalizes_dependencies asserts initialParameters.task.dependsOn |
| FR-012 | DOC-REQ-010 | service.py per-case error strings | Per-test message assertions in each error test |
