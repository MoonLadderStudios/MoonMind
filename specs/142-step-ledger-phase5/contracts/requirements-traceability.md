# Requirements Traceability: Step Ledger Phase 5

| Source Requirement | Spec Mapping | Planned Implementation | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-004 | Mutate the workflow-owned step row to `reviewing` during approval-policy work and preserve attempt semantics across retries | Workflow-boundary tests assert live `reviewing` transitions and final attempt counts |
| DOC-REQ-002 | FR-002 | Populate `checks[]` with `approval_policy` verdict state, bounded summaries, retry counts, and artifact refs | Workflow-boundary tests assert check-row shape; UI tests assert rendering |
| DOC-REQ-003 | FR-003 | Write full review request/verdict/issue payloads to artifacts and store only bounded summaries in workflow state | Workflow-boundary tests assert `artifactRef` linkage and bounded row content |
| DOC-REQ-004 | FR-001, FR-004, FR-005 | Execute review after eligible steps, retry failed reviews with feedback injection, and accept `INCONCLUSIVE` results | Workflow-boundary tests cover PASS, FAIL→retry→PASS, and INCONCLUSIVE |
| DOC-REQ-005 | FR-002, FR-005, FR-006 | Surface operator-visible review state, verdicts, and retry progress on the step row and in Mission Control | Workflow-boundary tests plus targeted task-detail UI tests |
| DOC-REQ-006 | FR-006 | Extend the existing Checks section rather than inventing a new detail surface | Task-detail tests assert review metadata appears inside Checks |
| DOC-REQ-007 | FR-006 | Keep compact badge/readability patterns consistent with Mission Control semantic styling | UI rendering checks plus targeted frontend verification |
| DOC-REQ-008 | FR-001, FR-007 | Add workflow-boundary coverage for `reviewing` lifecycle transitions and replay-safe step-ledger mutation | `pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q` |
