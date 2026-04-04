# Remaining work: `docs/UI/MissionControlArchitecture.md`

Updated: 2026-04-04

## Step-ledger rollout

- Implement the Steps section on task detail above Timeline and generic Artifacts.
- Fetch `/api/executions/{workflowId}/steps` after execution detail and before execution-wide artifact browsing.
- Support expanded step rows with Summary, Checks, Logs & Diagnostics, Artifacts, and Metadata groups.
- Add browser-facing tests for latest-run-only steps, delayed `taskRunId` arrival, and step-scoped observability attachment.
