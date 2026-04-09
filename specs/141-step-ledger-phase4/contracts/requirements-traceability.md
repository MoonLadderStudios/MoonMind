# Requirements Traceability: Step Ledger Phase 4

| Source Requirement | Spec Mapping | Planned Implementation | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-003 | Render latest-run step rows from `/api/executions/{workflowId}/steps` as the primary task-detail execution surface | Browser tests validate latest-run-only steps and visible current run id context |
| DOC-REQ-002 | FR-001 | Reframe task detail around step ledger state, checks, and evidence on the detail page | Browser tests validate the Steps-first hierarchy and row content |
| DOC-REQ-003 | FR-001, FR-002 | Place the Steps section above Timeline and generic Artifacts | Browser tests assert section ordering |
| DOC-REQ-004 | FR-001, FR-002 | Fetch execution detail first, then the step ledger, while keeping generic artifact reads secondary | Browser tests assert fetch ordering and separate reads |
| DOC-REQ-005 | FR-003, FR-004, FR-006 | Render stable expanded row groups and attach row-level observability only when needed | Browser tests validate expanded groups and row-scoped observability calls |
| DOC-REQ-006 | FR-004, FR-005 | Use `taskRunId` as a row-level observability binding and show delayed-binding copy when absent | Browser tests validate bound/unbound step behavior and delayed attachment |
| DOC-REQ-007 | FR-003, FR-006 | Consume bounded summaries/checks/refs/artifacts without treating logs or workflow history as inline state | Browser tests validate checks/artifact groups and scoped observability requests |
| DOC-REQ-008 | FR-007 | Style dense step chips and compact check badges with the existing Mission Control design system | Browser rendering checks plus CSS/type/test verification |
| DOC-REQ-009 | FR-005, FR-008 | Add browser coverage for latest-run-only steps, delayed `taskRunId`, and step-scoped observability attachment | Targeted `task-detail` browser tests plus final verification commands |
