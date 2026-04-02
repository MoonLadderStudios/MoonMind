| DOC-REQ ID | Functional Requirement(s) | Planned Implementation | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-002, FR-003 | Persist post-save binding and execution-detail store fallback | Unit tests for execution detail with missing memo `taskRunId`. |
| DOC-REQ-002 | FR-001, FR-002 | Add `workflow_id` to `ManagedRunRecord` and launch persistence. | Runtime/store unit tests and integration launch test. |
| DOC-REQ-003 | FR-004 | Owner-aware checks in `/api/task-runs/*` routes. | Router tests for admin, owner, and cross-owner access. |
| DOC-REQ-004 | FR-006 | Task-detail polling/attach behavior keyed to execution-detail `taskRunId`. | Frontend test with delayed `taskRunId` arrival. |
| DOC-REQ-005 | FR-007 | Task-detail placeholder state derivation from execution detail. | Frontend tests for waiting, launch-failed, and binding-missing states. |
| DOC-REQ-006 | FR-007 | Explicit 403 handling in observability fetch helpers/panels. | Frontend test for observability 403 copy. |
| DOC-REQ-007 | FR-008 | Long-running managed launch integration validation through summary/merged tail. | Integration test with simulated long-running output. |
| DOC-REQ-008 | FR-006, FR-007 | Browser-facing test using real execution-detail payload shape without injected `taskRunId`. | Frontend delayed-attach test. |
