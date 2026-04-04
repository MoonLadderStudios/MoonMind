# Remaining work: `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`

Updated: 2026-04-04

## Step-ledger rollout

- Ensure `MoonMind.Run` owns compact step truth and progress while `MoonMind.AgentRun` remains the true runtime lifecycle boundary.
- Keep Memo/Search Attribute updates compact and user-visible rather than log- or heartbeat-driven.
- Add lifecycle tests for plan resolved, step ready, step started, step reviewing, step succeeded, step failed, and step canceled transitions.
