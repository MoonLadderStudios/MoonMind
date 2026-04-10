# Requirements Traceability: Step Ledger Phase 1

| Requirement ID | Maps To FRs | Planned Implementation Surface | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-002 | Initialize ledger rows from `parse_plan_definition()` output in `moonmind/workflows/temporal/workflows/run.py` with helper support in `moonmind/workflows/temporal/step_ledger.py` | Unit tests assert plan-node metadata populates `logicalStepId`, `title`, `tool`, and dependencies |
| DOC-REQ-002 | FR-002, FR-003 | Add workflow-owned ledger state to `MoonMind.Run` using `moonmind/workflows/temporal/step_ledger.py` | Workflow-boundary tests assert state transitions and query output |
| DOC-REQ-003 | FR-006 | Keep only bounded refs/placeholders in workflow state and avoid storing logs/diagnostics bodies | Unit tests verify artifact slots/ref fields are scalar refs/defaults only |
| DOC-REQ-004 | FR-005 | Query responses include stable `workflowId`, active `runId`, and `runScope: "latest"` | Workflow query tests assert latest-run semantics during execution and after completion |
| DOC-REQ-005 | FR-001, FR-005 | Add canonical bounded progress model to `moonmind/schemas/temporal_models.py` and workflow reducer | Golden fixture tests plus workflow progress query coverage |
| DOC-REQ-006 | FR-001, FR-005 | Add canonical step-ledger snapshot/row models and workflow query payloads | Golden fixture tests plus workflow query coverage |
| DOC-REQ-007 | FR-001, FR-006, FR-008 | Freeze row field set including checks/refs/artifacts placeholders | Schema validation tests for representative row fixtures |
| DOC-REQ-008 | FR-001, FR-003 | Encode canonical status vocabulary in schema/helper layer and workflow transitions | Transition tests cover each required status |
| DOC-REQ-009 | FR-001, FR-008 | Keep `checks[]` as a structured field on rows even when empty/default | Schema fixture tests assert `checks` presence and shape |
| DOC-REQ-010 | FR-004 | Track attempts in workflow state keyed by logical step and current run | Retry-oriented workflow tests assert attempt increments without cross-run merge |
| DOC-REQ-011 | FR-007 | Restrict Memo/Search Attribute writes to compact summary data in `run.py` | Tests assert no full rows/attempts/checks are mirrored |
| DOC-REQ-012 | FR-003, FR-009 | Add workflow lifecycle transition coverage for ready/running/waiting/reviewing/terminal states | Workflow-boundary tests cover the full transition matrix |
| DOC-REQ-013 | FR-001 | Freeze bounded progress and ledger contract now so later API routes can reuse it | Golden fixture tests and schema model validation |
