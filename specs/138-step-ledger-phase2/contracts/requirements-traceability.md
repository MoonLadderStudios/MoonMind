# Requirements Traceability: Step Ledger Phase 2

| Source Requirement | Spec Mapping | Planned Implementation | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-002 | Enrich child results with workflow/task-run lineage and project them into parent step rows | Workflow boundary tests for child refs |
| DOC-REQ-002 | FR-002, FR-003 | Group result metadata into canonical step artifact slots inside `MoonMind.Run` | Workflow boundary tests for artifact slot grouping |
| DOC-REQ-003 | FR-005 | Stamp `step_id`, `attempt`, and `scope` in published artifact metadata | Activity unit test capturing artifact metadata |
| DOC-REQ-004 | FR-004 | Keep workflow state bounded and avoid log bodies in row/memo/search attributes | Step-ledger tests asserting refs only |
| DOC-REQ-005 | FR-001, FR-003, FR-004 | Surface managed-run/session observability refs without duplicating logs | Activity + workflow tests |
| DOC-REQ-006 | FR-003, FR-005 | Use artifact-backed summary/result publication plus step-scoped metadata | Activity unit test plus workflow grouping tests |
| DOC-REQ-007 | FR-001 | Carry the managed-run observability handle into parent step rows as `taskRunId` | Agent-run/runtime tests and workflow step-row assertions |
| DOC-REQ-008 | FR-002, FR-006 | Reuse the Phase 1 row schema unchanged while filling reserved fields | Schema-preserving workflow tests |
