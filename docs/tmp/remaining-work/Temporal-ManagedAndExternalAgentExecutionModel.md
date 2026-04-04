# Remaining work: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`

Updated: 2026-04-04

## Step-ledger rollout

- Persist and surface parent-step refs from `MoonMind.AgentRun` back to `MoonMind.Run`, including `childWorkflowId`, `childRunId`, and `taskRunId`.
- Keep managed-run observability authoritative in `/api/task-runs/*`; do not duplicate log state into parent workflow payloads.
- Add compatibility tests covering parent/child step refs and managed-run observability attachment on plan-driven task detail.
