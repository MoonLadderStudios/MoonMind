# Temporal Workflow Message Passing - Phase 1 Inventory

This document provides a code-level map of Temporal usage in the MoonMind repository, fulfilling Phase 1 of the Temporal Workflow Message Passing Improvements plan.

## 1. Workflows

| Workflow Type | Decorator Location | Primary Purpose |
|---------------|-------------------|-----------------|
| `MoonMind.Run` | `moonmind/workflows/temporal/workflows/run.py` | Core orchestrator for task execution and agent runs |
| `MoonMind.AgentRun` | `moonmind/workflows/temporal/workflows/agent_run.py` | Orchestrates a specific agent/runtime within a run |
| `MoonMind.ManifestIngest` | `moonmind/workflows/temporal/manifest_ingest.py` | Manages background ingestion of templates/manifests |
| `MoonMind.AuthProfileManager` | `moonmind/workflows/temporal/workflows/auth_profile_manager.py` | Syncs auth profiles to workers |
| `MoonMind.OAuthSession` | `moonmind/workflows/temporal/workflows/oauth_session.py` | Manages OAuth device code flow and tokens |
| `MoonMind.Task514` | `moonmind/workflows/temporal/workflows/task_5_14_workflow.py` | Testing/utility workflow |

## 2. Activities

| Activity Name | Decorator Location | Purpose |
|---------------|-------------------|---------|
| `integration.jules.*` | `moonmind/workflows/temporal/activities/jules_activities.py` | Jules API operations (start, status, etc) |
| `integration.codex_cloud.*` | `moonmind/workflows/temporal/activities/codex_cloud_activities.py` | Codex Cloud API operations |
| `oauth_session.*` | `moonmind/workflows/temporal/activities/oauth_session_activities.py` | OAuth session lifecycle events |
| `oauth_session.cleanup_stale` | `moonmind/workflows/temporal/activities/oauth_session_cleanup.py` | Sweeping old sessions |
| `integration.resolve_external_adapter` | `moonmind/workflows/temporal/workflows/agent_run.py` | Agent adapter lookup |
| `integration.external_adapter_execution_style` | `moonmind/workflows/temporal/workflows/agent_run.py` | Determines polling vs streaming |

## 3. Message Handlers (Queries, Signals, Updates)

### MoonMind.Run
* **Signals:**
  * `cancel`: Requests cancellation of the run.
  * `pause`: Requests pausing the run.
  * `resume`: Requests resuming a paused run.
  * `approve`: Approves a pending operation.
  * `ExternalEvent`: For incoming webhook/callback events.
  * `child_state_changed`: For child workflows to report state changes.
* **Updates:**
  * `update_title`: Updates the workflow's title.
  * `update_parameters`: Updates the workflow's parameters.

### MoonMind.AgentRun
* **Signals:**
  * `completion_signal`: Notifies agent has completed (used with external webhooks).
  * `slot_assigned`: Notifies that an auth profile slot has been assigned.

### MoonMind.AuthProfileManager
* **Queries:**
  * `get_state`: Returns current sync status and slot leases.
* **Signals:**
  * `request_slot`: An AgentRun requests a profile slot.
  * `release_slot`: An AgentRun releases its profile slot.
  * `report_cooldown`: Reports a 429 cooldown on a profile.
  * `sync_profiles`: Pushes updated profiles payload to the manager.
  * `shutdown`: Requests a graceful shutdown of the manager.

### MoonMind.OAuthSession
* **Queries:**
  * `get_status`: Returns current token/session state.
* **Signals:**
  * `cancel`: Cancels the pending OAuth session.
  * `finalize`: Finalizes the OAuth session with a token.

### MoonMind.ManifestIngest
* **Updates:**
  * `UpdateManifest`: Triggers specific manifest update.
  * `SetConcurrency`: Adjusts concurrent ingestion limit.
  * `Pause` / `Resume`: Control flow.
  * `CancelNodes` / `RetryNodes`: Granular error recovery.

## 4. Task Queues

| Queue String | Usage Location | Purpose |
|--------------|----------------|---------|
| `mm.activity.artifacts` | Workers / clients | Used for artifact manipulation activities |
| `mm.workflow` | Implicitly via WORKFLOW_TASK_QUEUE | Default for Core Workflows (Run, AgentRun) |

## 5. Child Workflow Relationships

* `MoonMind.ManifestIngest` -> executes `MoonMind.Run` (for specific ingestion tasks)
* `MoonMind.Run` -> executes `MoonMind.AgentRun` (for the actual tool/agent execution phase)

## 6. Client API Usage Matrix

| API Action / Service | Temporal Client Operation | Target Workflow |
|----------------------|---------------------------|-----------------|
| `api_service/main.py` | `start_workflow` | Startup / System Workflows |
| `AuthProfileService` | `start_workflow` | `MoonMind.AuthProfileManager` |
| `AuthProfileService` | `get_workflow_handle().signal("sync_profiles")` | `MoonMind.AuthProfileManager` |
| `OAuthSessionService` | `start_workflow` | `MoonMind.OAuthSession` |
| `OAuthSessionService` | `get_workflow_handle().signal("cancel")` | `MoonMind.OAuthSession` |
| `ExecutionsRouter` | `signal_execution` (REST Endpoint) | Any active workflow |

## 7. Gaps vs "Missing Features Checklist"

Based on the inventory:
* **Workflow Updates:** They ARE used in `ManifestIngest` and `MoonMind.Run` (partially). However, `AuthProfileManager` and `OAuthSession` still heavily rely on Signals for state changes.
* **Query Safety:** Queries appear read-only but `get_status` and `get_state` handlers need manual auditing to confirm no state mutation.
* **Child Workflows:** Used correctly (e.g., `ManifestIngest` -> `Run` and `Run` -> `AgentRun`).
