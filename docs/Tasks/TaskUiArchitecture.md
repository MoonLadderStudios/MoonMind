# Task UI Architecture

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-13  

## 1. Purpose

Define the concrete implementation architecture for the MoonMind Mission Control UI component tree and routing schema. The dashboard integrates securely over the Control Plane API, interpreting Temporal execution statuses.

## 2. Implementation Snapshot

The dashboard is a thin, server-hosted web app built without heavy bundlers:
- HTML shell: `api_service/templates/task_dashboard.html`
- Client app: `api_service/static/task_dashboard/dashboard.js`
- Runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`

## 3. Route Map

| Route | Purpose |
| --- | --- |
| `/tasks/list` | Unified task list viewing workflow executions (Temporal Visibility) |
| `/tasks/new` | Unified submit page / Workflow form wizard |
| `/tasks/queue/new` | Alias to `/tasks/new`; prefill mode uses `?editJobId=<jobId>` |
| `/tasks/:taskId` | Unified task detail shell resolving workflow history |
| `/tasks/proposals` | Proposal queue list and triage actions |
| `/tasks/proposals/:proposalId` | Proposal detail, promote/dismiss/priority/snooze actions |

## 4. Detail View Lifecycle

When viewing `/tasks/:taskId`, the dashboard polls the API (which maps to Temporal's Execution history or Postgres index).

- Queue-backed fetches query standard `GET /api/queue/jobs/{jobId}` which derives status from the execution index.
- Operator actions (Approve, Resume, Pause) interact with task limits by submitting to standard API routes, which issue Signals to the `MoonMind.Run` handler.
- Terminal outputs manifest in a UI Finish Summary block, mapping standard events into human-readable outcome strings (like `NO_CHANGES` or `PUBLISHED_PR`).
