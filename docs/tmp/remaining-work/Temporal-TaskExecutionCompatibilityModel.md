# Remaining work: `docs/Temporal/TaskExecutionCompatibilityModel.md`

Updated: 2026-04-04

## Step-ledger rollout

- Add compatibility payload wiring for bounded `progress` plus `stepsHref` on Temporal-backed task detail.
- Keep compatibility detail task-oriented while avoiding raw Temporal event history or log-derived step heuristics.
- Add adapter tests proving task detail still resolves by `taskId == workflowId` while step detail is loaded separately.
