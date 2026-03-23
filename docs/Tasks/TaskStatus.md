# Task Status Model

How workflow status flows from Temporal through MoonMind to the dashboard.

## Design Principle

> **DB state string = dashboard status string.** The `MoonMindWorkflowState` enum value and the dashboard display status should be the same string. No translation layer should be required.

## Temporal vs MoonMind Status

Temporal itself only has **5 terminal/non-terminal statuses**: `Running`, `Completed`, `Failed`, `Canceled`, `Terminated`. All finer-grained status is an application concern.

MoonMind adds its own `MoonMindWorkflowState` enum stored in the DB and synced via Temporal search attributes (`mm_state`). The dashboard then normalizes these to a smaller set of display statuses.

## Current Model (After Immediate Changes)

Renames applied: `AWAITING` → `AWAITING_SLOT`, `SUCCEEDED` → `COMPLETED`.

| Temporal Status | MoonMind DB State | Dashboard Status | 1:1? | Meaning |
|---|---|---|---|---|
| Running | `SCHEDULED` | queued | ❌ | Workflow created, not yet started |
| Running | `INITIALIZING` | queued | ❌ | Setting up, brief bootstrap |
| Running | `AWAITING_SLOT` | queued | ❌ | Waiting for auth-profile slot |
| Running | `WAITING_ON_DEPENDENCIES` | waiting | ❌ | Blocked on prerequisite tasks |
| Running | `PLANNING` | running | ❌ | Generating execution plan |
| Running | `EXECUTING` | running | ❌ | Agent actively working |
| Running | `AWAITING_EXTERNAL` | awaiting_action | ❌ | Blocked on external provider |
| Running | `FINALIZING` | running | ❌ | Wrapping up, collecting results |
| Completed | `COMPLETED` | completed | ✅ | Finished successfully |
| Failed | `FAILED` | failed | ✅ | Terminated with error |
| Canceled | `CANCELED` | cancelled | ❌ | Canceled by user or system |

States not yet 1:1 require a normalization layer. The goal is to eliminate that layer.

## Target Architecture: Full 1:1 DB ↔ Dashboard

Eliminate the normalization layer entirely. Internal workflow phases (`planning`, `executing`, `finalizing`) move to a Temporal search attribute (`mm_phase`).

| MoonMind DB State | Dashboard Status | Notes |
|---|---|---|
| `QUEUED` | queued | Absorbs current `SCHEDULED`, `INITIALIZING`, and `AWAITING` |
| `WAITING` | waiting | Replaces `WAITING_ON_DEPENDENCIES` |
| `RUNNING` | running | Absorbs `PLANNING`, `EXECUTING`, `FINALIZING` |
| `AWAITING_EXTERNAL` | awaiting_action | Blocked on external provider |
| `SUCCEEDED` | succeeded | Terminal |
| `FAILED` | failed | Terminal |
| `CANCELED` | cancelled | Terminal |

Phase detail (planning/executing/finalizing) would be tracked via:
- **Temporal search attribute** `mm_phase` for queryability
- **Workflow queries** for real-time inspection
- **Memo fields** for display in the detail view

> [!NOTE]
> This 1:1 refactor is a larger effort that touches `service.py` state transitions, `compatibility.py` filter queries, and the sync layer. It should be done as a dedicated spec.

## Future: Temporal Schedules Integration

Temporal has a dedicated **Schedules API** for time-triggered workflows. When MoonMind adopts this:

- `SCHEDULED` would no longer be a workflow state — it becomes a separate **Schedule entity** managed by the Schedules API
- When a schedule fires, it creates a workflow execution starting in `QUEUED` (if slot-blocked) or `RUNNING` (if resources available)
- The dashboard would show scheduled tasks from the Schedules API, not from `MoonMindWorkflowState`

## Key Definitions

| Term | Scope | Meaning |
|---|---|---|
| **Queued** | DB + Dashboard | Ready to run but waiting for resources (auth-profile slot) |
| **Waiting** | DB + Dashboard | Blocked on prerequisite tasks completing first |
| **Awaiting External** | DB | Blocked on an external provider (Jules, OAuth, integration) |
| **Awaiting Action** | Dashboard only | Normalized display name for `AWAITING_EXTERNAL` |
| **Awaiting Callback** | Agent run only | Agent submitted work, waiting for first provider signal |
| **Awaiting Feedback** | Agent run only | Agent paused, waiting for user input |
