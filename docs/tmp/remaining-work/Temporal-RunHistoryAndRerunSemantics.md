# Remaining work: `docs/Temporal/RunHistoryAndRerunSemantics.md`

**Source:** [`docs/Temporal/RunHistoryAndRerunSemantics.md`](../../Temporal/RunHistoryAndRerunSemantics.md)  
**Last synced:** 2026-03-24

## Open items

- **Product surface:** Run history is not yet first-class; doc “done when” criteria in §§5–6 remain partially unmet.
- **§13 Follow-on implementation hooks:**
  - Expose `rerunCount` in execution detail only if the dashboard needs it; document semantics (Continue-As-New vs manual rerun).
  - Optional ops-only run-history endpoint.
  - Stable task/detail URLs across Continue-As-New `runId` rotation.
  - Tests asserting rerun preserves `workflowId` and rotates `runId`.
- **Naming during migration:** Clean up any transitional naming once compatibility window closes.
