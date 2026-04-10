# Requirements Traceability: Step Ledger Phase 3

| Source Requirement | Spec Mapping | Planned Implementation | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-002, FR-004 | Query latest-run progress and step-ledger state by `workflowId` while keeping detail reads bounded | Router and contract tests prove latest-run progress and step-ledger reads stay keyed by `workflowId` |
| DOC-REQ-002 | FR-002 | Reuse the frozen `StepLedgerSnapshotModel` contract unchanged on the `/steps` route | Steps route tests validate attempts, refs, and artifact slots without reshaping rows |
| DOC-REQ-003 | FR-001, FR-003, FR-006 | Add bounded `progress` to `ExecutionModel` and regenerate the client contract | Execution detail tests plus generated OpenAPI diff validate `progress` exposure |
| DOC-REQ-004 | FR-002, FR-006 | Implement `GET /api/executions/{workflowId}/steps` as a first-class route | Router and contract tests validate the `/steps` route and response shape |
| DOC-REQ-005 | FR-005, FR-006 | Extend compatibility detail and runtime config with `stepsHref` / temporal steps endpoint metadata | Serialization and runtime-config tests validate `stepsHref` exposure |
| DOC-REQ-006 | FR-004 | Preserve latest-run semantics across Continue-As-New on detail and steps reads | API tests validate latest-run `runId` behavior across rerun / Continue-As-New |
| DOC-REQ-007 | FR-004, FR-007 | Add router and contract coverage for progress, status vocabulary, attempts, and latest-run semantics | Targeted API/contract tests plus full unit-suite validation |
| DOC-REQ-008 | FR-003, FR-005 | Keep task detail bounded and task-oriented while linking to step detail separately | Compatibility tests validate bounded detail payloads and `taskId == workflowId` behavior |
