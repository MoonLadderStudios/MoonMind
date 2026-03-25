# Temporal Workflow Message Passing Inventory

This document fulfills Phase 1 of the Temporal Workflow Message Passing Improvements plan, providing a code-level map of Temporal usage in the MoonMind repository.

## 1. Inventory of Workflows, Activities, and Queues

### Workflows and Handlers

| Workflow Type | Decorator Location | Primary Purpose | Message Handlers |
|---------------|-------------------|-----------------|------------------|
| `MoonMind.Run` | `moonmind/workflows/temporal/workflows/run.py` | Core orchestrator for task execution and agent runs | **Signals:** `cancel`, `pause`, `resume`, `approve`, `ExternalEvent`, `child_state_changed`<br>**Updates:** `update_title`, `update_parameters`<br>**Queries:** `get_status` |
| `MoonMind.AgentRun` | `moonmind/workflows/temporal/workflows/agent_run.py` | Orchestrates a specific agent/runtime within a run | **Signals:** `completion_signal`, `slot_assigned` |
| `MoonMind.ManifestIngest` | `moonmind/workflows/temporal/manifest_ingest.py` | Manages background ingestion of templates/manifests | **Updates:** `UpdateManifest`, `SetConcurrency`, `Pause`, `Resume`, `CancelNodes`, `RetryNodes` |
| `MoonMind.AuthProfileManager` | `moonmind/workflows/temporal/workflows/auth_profile_manager.py` | Syncs auth profiles to workers | **Signals:** `request_slot`, `release_slot`, `report_cooldown`, `sync_profiles`, `shutdown`<br>**Queries:** `get_state` |
| `MoonMind.OAuthSession` | `moonmind/workflows/temporal/workflows/oauth_session.py` | Manages OAuth device code flow and tokens | **Signals:** `cancel`, `finalize`<br>**Queries:** `get_status` |
| `MoonMind.Task514` | `moonmind/workflows/temporal/workflows/task_5_14_workflow.py` | Testing/utility workflow | |

### Activities

| Category | Activities | Purpose |
|----------|------------|---------|
| **OAuth Session** | `oauth_session.cleanup_stale`, `.ensure_volume`, `.start_auth_runner`, `.stop_auth_runner`, `.update_status`, `.mark_failed` | OAuth session lifecycle events |
| **Jules Integration** | `integration.jules.start`, `.status`, `.fetch_result`, `.cancel`, `.send_message`, `.list_activities`, `.answer_question`, `.merge_pr`, `.get_auto_answer_config` | Jules API operations |
| **Codex Cloud** | `integration.codex_cloud.start`, `.status`, `.fetch_result`, `.cancel` | Codex Cloud API operations |
| **Agent/Adapter** | `integration.resolve_external_adapter`, `integration.external_adapter_execution_style`, `agent_runtime.publish_artifacts`, `agent_runtime.cancel` | Agent adapter lookup and execution |
| **General/Runtime** | `plan.generate`, `artifact.read`, `mm.skill.execute`, `sandbox.run_command`, `proposal.generate`, `proposal.submit` | Dynamic skill and sandbox execution |
| **Tests** | `task_5_14_activity` | Testing |

### Task Queues

| Queue String | Purpose |
|--------------|---------|
| `mm.workflow` | Default for Core Workflows (Run, AgentRun) |
| `mm.activity.artifacts` | Artifact manipulation activities |
| `mm.activity.llm` | LLM interactions |
| `mm.activity.sandbox` | Sandbox executions |
| `mm.activity.integrations` | External integrations |
| `mm.activity.agent_runtime` | Agent runtime coordination |
| `AUTH_PROFILE_MANAGER_QUEUE` | Auth profile sync |
| `ACTIVITY_TASK_QUEUE` | Generic activities |

### Child Workflow Relationships

* `MoonMind.ManifestIngest` -> spawns `MoonMind.Run` (for specific ingestion tasks)
* `MoonMind.Run` -> spawns execution-specific workflows (`MoonMind.AgentRun`)

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
9. **Query Safety.**
   - **GAP**: Queries appear read-only but `get_status` and `get_state` handlers need manual auditing to confirm no state mutation.

## Exit Criteria Status
- **Inventory Checked In**: Yes (this document).
- **Stakeholders Agree on Orchestrators**: `MoonMind.Run` and `MoonMind.AgentRun` are confirmed as primary job/task orchestrators, with others acting as supporting workflows (e.g., AuthProfileManager, ManifestIngest).