# Temporal Workflow Message Passing Inventory

This document fulfills Phase 1 of the Temporal Workflow Message Passing Improvements plan.

## 1. Inventory of Workflows, Activities, and Queues

### Workflows (`@workflow.defn`)
- `MoonMind.ManifestIngest` (`moonmind/workflows/temporal/manifest_ingest.py`, `moonmind/workflows/temporal/workflows/manifest_ingest.py`)
  - **Updates**: `UpdateManifest`, `SetConcurrency`, `Pause`, `Resume`, `CancelNodes`, `RetryNodes`
- `MoonMind.AgentRun` (`moonmind/workflows/temporal/workflows/agent_run.py`)
  - **Signals**: (2 signals including `completion_signal`)
- `MoonMind.AuthProfileManager` (`moonmind/workflows/temporal/workflows/auth_profile_manager.py`)
  - **Signals**: (5 signals)
  - **Queries**: (1 query)
- `MoonMind.Run` (`moonmind/workflows/temporal/workflows/run.py`)
  - **Signals**: `pause`, `resume`, `cancel`, `approve`, `ExternalEvent`, `child_state_changed`
  - **Updates**: `update_parameters`, `update_title`
  - **Queries**: `get_status` (pending/added)
- `OAuthSessionWorkflow` (`moonmind/workflows/temporal/workflows/oauth_session.py`)
  - **Signals**: (2 signals)
  - **Queries**: (1 query)
- `Task5_14Workflow` (`moonmind/workflows/temporal/workflows/task_5_14_workflow.py`)

### Activities (`@activity.defn`)
- **OAuth Session**: `oauth_session.cleanup_stale`, `oauth_session.ensure_volume`, `oauth_session.start_auth_runner`, `oauth_session.stop_auth_runner`, `oauth_session.update_status`, `oauth_session.mark_failed`
- **Jules Integration**: `integration.jules.start`, `.status`, `.fetch_result`, `.cancel`, `.send_message`, `.list_activities`, `.answer_question`, `.merge_pr`, `.get_auto_answer_config`
- **Codex Cloud Integration**: `integration.codex_cloud.start`, `.status`, `.fetch_result`, `.cancel`
- **Agent/Adapter**: `integration.resolve_external_adapter`, `integration.external_adapter_execution_style`, `agent_runtime.publish_artifacts`, `agent_runtime.cancel`
- **General/Runtime** (dynamic/referenced): `plan.generate`, `artifact.read`, `mm.skill.execute`, `sandbox.run_command`, `proposal.generate`, `proposal.submit`
- **Tests**: `task_5_14_activity`

### Task Queues
- `mm.workflow`
- `mm.activity.artifacts`
- `mm.activity.llm`
- `mm.activity.sandbox`
- `mm.activity.integrations`
- `mm.activity.agent_runtime`
- `AUTH_PROFILE_MANAGER_QUEUE`
- `ACTIVITY_TASK_QUEUE`

### Child Workflow Relationships
- `MoonMind.Run` spawns execution-specific workflows.
- `MoonMind.AgentRun` orchestrates specialized workers/activities (e.g., integrations, sandbox).

## 2. Action Matrix (API & Mission Control vs. Temporal)

| Action | Source | Temporal Mechanism | Target Workflow | Notes |
|---|---|---|---|---|
| Submit Job | API | `start_workflow` | `MoonMind.Run` / `MoonMind.AgentRun` | Initializes task state |
| Pause Job | Mission Control/API | `@workflow.signal` | `MoonMind.Run` | Current gap: uses signal instead of update |
| Resume Job | Mission Control/API | `@workflow.signal` | `MoonMind.Run` | Current gap: uses signal instead of update |
| Cancel Job | Mission Control/API | `@workflow.signal` | `MoonMind.Run` | Current gap: uses signal instead of update |
| Update Title/Params | Mission Control | `@workflow.update` | `MoonMind.Run` | Idiomatic use of Update |
| Read Status | Mission Control | `@workflow.query` / DB | `MoonMind.Run` | (e.g. `get_status`) |
| Ingest Manifest | API | `start_workflow` / Update | `MoonMind.ManifestIngest` | Rich update interface |

## 3. Gaps List Against Missing Features Checklist

1. **Workflow Updates used for command-style message passing (trackable) rather than only Signals.**
   - **GAP**: `MoonMind.Run` uses signals (`pause`, `resume`, `cancel`) for control-plane commands instead of `@workflow.update` with validators.
2. **Activity timeouts + retry policies on all external calls, and heartbeats for long steps.**
   - **GAP**: Needs systematic audit. Many `@activity.defn` instances lack explicit heartbeat implementation in long-running integrations.
3. **Continue-As-New for long-lived, step-heavy jobs.**
   - **GAP**: No prominent usage of Continue-As-New observed in the orchestrator workflows (`MoonMind.Run`).
4. **Child workflows for large sub-problems and isolation.**
   - **GAP**: Need to enforce child workflows for sandbox sessions to keep histories bounded.
5. **Schedules (vs ad hoc cron) for recurring jobs.**
   - **GAP**: Needs to be migrated to Temporal Schedules if currently using ad-hoc cron implementations.
6. **Versioning strategy (patching and/or worker versioning) for safe evolution.**
   - **GAP**: Missing robust usage of `patched` / `deprecate_patch` in evolving workflows like `MoonMind.Run`.
7. **Observability (metrics, tracing) and UI enrichment for operations.**
   - **GAP**: UI enrichment (Summary/Details) needs standardization across all job types.
8. **Encryption/codecs for payload confidentiality where required.**
   - **GAP**: Payload encryption is not systematically implemented for sensitive artifacts/prompts.

## Exit Criteria Status
- **Inventory Checked In**: Yes (this document).
- **Stakeholders Agree on Orchestrators**: `MoonMind.Run` and `MoonMind.AgentRun` are confirmed as primary job/task orchestrators, with others acting as supporting workflows (e.g., AuthProfileManager, ManifestIngest).