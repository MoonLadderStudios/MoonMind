# Requirements Traceability: Step Ledger Phase 6

| Source Requirement | Spec Mapping | Planned Implementation | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-002, FR-004 | Reconcile execution detail and task-detail latest-run reads to the current workflow run while preserving latest-run-only semantics | Router, contract, and browser tests assert latest-run identity and latest-run-scoped artifact reads |
| DOC-REQ-002 | FR-001, FR-003, FR-004 | Keep `workflowId` stable while rerun / Continue-As-New rotates `runId` and the default detail view follows the current run | Contract and service coverage assert rerun / Continue-As-New latest-run behavior |
| DOC-REQ-003 | FR-001, FR-003 | Prefer workflow query truth over stale projection `runId` values without fabricating degraded-read state | Router and contract tests assert queried latest-run reconciliation and honest degradation |
| DOC-REQ-004 | FR-002 | Keep execution-wide artifact browsing aligned to the latest resolved run rather than a stale detail snapshot | Targeted browser test asserts artifact fetches switch to the latest run from the step ledger |
| DOC-REQ-005 | FR-002, FR-004 | Preserve the Steps-first Mission Control model while keeping secondary evidence panels truthful | Task-detail tests assert latest-run Steps remain primary and artifacts stay aligned |
| DOC-REQ-006 | FR-005 | Remove completed step-ledger rollout bullets from tmp tracker files once Phase 6 lands | Tracker-file diff plus spec-package validation confirm the rollout bullets are retired |
