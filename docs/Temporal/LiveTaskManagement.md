# Live Task Management

**Implementation tracking:** [`docs/tmp/050-TmatePlan.md`](../tmp/050-TmatePlan.md) · [`specs/024-live-task-handoff`](../../specs/024-live-task-handoff/)

Status: Draft  
Owners: MoonMind Engineering  
Last Updated: 2026-03-26

---

## 1. Purpose

Operators need real-time visibility into running tasks. This document defines two complementary capabilities:

1. **Live Log Tailing** — An on-demand, read-only view of the most recent terminal output from a running task, rendered in the Mission Control task detail page (artifact-backed; see [LiveLogs.md](../ManagedAgents/LiveLogs.md)).
2. **Live Terminal Handoff** — Interactive operator controls (pause/resume, grants, messages) layered on the same run, without requiring a third-party terminal relay in the managed runtime image.

Implementation detail: the managed launcher runs agents as a plain subprocess with piped streams; live output is not produced by wrapping the agent in an external terminal multiplexer.

---

## 2. Goals

1. Show operators what the agent is doing right now — without requiring a separate terminal client.
2. Provide a rolling window of recent terminal output (last ~200 lines) in the Mission Control UI.
3. Allow operators to escalate from passive observation to active intervention when needed.
4. Default to secure behavior: read-only first, time-bound write grants, revocation, and audit trails.
5. Minimize new surface area: prefer first-party artifact and API contracts over embedding external terminal viewers.

---

## 3. Non-Goals

- Replacing artifact logs or workflow history; these capabilities are additive.
- Providing a collaborative web IDE experience (code-server, VS Code, etc.).
- Solving agent correctness; this is about enabling human observation and intervention.

---

## 4. Shared infrastructure (current)

Real-time visibility is delivered without an external terminal relay in the default managed path:

1. **Managed runtime** — `ManagedRuntimeLauncher` spawns a direct subprocess; `ManagedRunSupervisor` streams stdout/stderr into run-scoped artifacts. Contracts and UI integration are described in [LiveLogs.md](../ManagedAgents/LiveLogs.md).
2. **Task-run live sessions** — Optional metadata for operator tooling lives in **`task_run_live_sessions`**. Workers report via **`POST /api/task-runs/{id}/live-session/report`** and heartbeats; operators read **`GET /api/task-runs/{id}/live-session`**. Provider **`none`** applies when no external relay is in use.
3. **Queue worker** — The standalone worker may report live-session state over HTTP when enabled; it does not provision a relay when the configured provider is `none`.

### 4.1 Session lifecycle (conceptual)

```
DISABLED → STARTING → READY → (REVOKED | ENDED | ERROR)
```

- **DISABLED**: No live session or provider `none`.
- **STARTING**: Worker registered intent to report session metadata.
- **READY**: Worker-advertised RO/RW fields (if any) are available to authorized clients.
- **REVOKED / ENDED / ERROR**: Session closed; agent execution may continue without a relay.

### 4.2 Workspace artifacts

Under `/work/agent_jobs/<workflow_id>/artifacts/`:

- `stdout.log` / `stderr.log` — Streamed process output (primary source for Live Output when using artifact APIs).
- `operator_inbox.jsonl` — Append-only JSON lines for operator messages.
- `diagnostics.json` — Supervision metadata.
- `transcript.log` — Optional captured transcript when enabled.

### 4.3 Image requirements

Runtime images should include `openssh-client` and `ca-certificates` for Git and CLI networking. No relay-specific terminal packages are required for default log visibility.

---

## 5. Live Log Tailing

### 5.1 Concept

The Mission Control task detail page includes a collapsible **Live Output** panel. When the operator toggles it open:

1. The UI fetches the most recent ~200 lines of terminal output from the running session.
2. New lines stream in continuously, pushing older lines off the top of the buffer.
3. The operator sees a live tail of the agent's terminal — like `tail -f` in the browser.

When the panel is collapsed or the tab is backgrounded, polling stops.

### 5.2 Data Source

The UI reads recent terminal output from **artifact-backed log APIs** (or equivalent streaming endpoints) rather than embedding a third-party terminal web viewer. Scrollback and rendering use Mission Control components; see [LiveLogs.md](../ManagedAgents/LiveLogs.md).

### 5.3 UI Behavior

- **Default state**: Panel collapsed, no connection established.
- **On toggle open**: Load the live log stream. Show a loading indicator while data is fetched.
- **While open**: New log lines append; older lines fall out of the rolling window per product limits.
- **On toggle close**: Disconnect from the stream. No background resource usage.
- **Background tab**: Disconnect or pause the stream when the tab loses visibility (via `visibilitychange` event).
- **Terminal workflows**: Show "Session ended" with no stream. If a `transcript.log` artifact exists, offer a download link.
- **No session available**: Show "Live output is not available for this task" (session in `DISABLED` or `ERROR` state).

### 5.4 API Contract

The live session metadata endpoint already provides the web RO URL:

```
GET /api/workflows/{id}/live-session
```

Response (when `status = READY`):

```json
{
  "status": "READY",
  "webRo": "https://example.invalid/readonly-view",
  "attachRo": "ssh -o … user@worker"
}
```

When `webRo` is present, the dashboard may offer a link; artifact APIs remain the default source of truth for tailing managed-runtime output.

---

## 6. Live Terminal Handoff

### 6.1 Concept

When passive observation isn't enough, operators can escalate to full interactive terminal access. This allows them to:

- Watch the live terminal with full fidelity (not just log lines).
- Send lightweight clarification messages to the agent without pausing.
- Take over execution: pause the agent, get write access, make fixes, resume.

### 6.2 Pause/Resume and Takeover Flow

- **Soft pause (default)**: The agent wrapper honors a `pause_workflow` Temporal Signal at safe checkpoints, prints `== PAUSED FOR OPERATOR ==`, and stops issuing new tool calls. The terminal remains live for RO viewers, and RW operators can use the dedicated pane without racing the agent.
- **Operator takeover**: Dashboard workflow is pause → grant RW (15-minute TTL by default) → attach via RW pane → perform fixes/tests → enter an operator summary → resume. The summary is appended to task context for traceability.

### 6.3 Operator Messages

Operator messages provide a clarification channel without granting write access. Dashboard writes append JSON lines such as:

```json
{"ts":"2026-03-17T21:45:12Z","actor":"nathaniel","message":"Try reducing step size to 0.5 and rerun the unit test suite."}
```

Pane 2 tails `operator_inbox.jsonl`, so RO observers and the agent itself can see new guidance immediately. Agents may optionally parse the inbox to incorporate instructions at the next checkpoint.

---

## 7. Persistence Model

### 7.1 `workflow_live_sessions` (Database)

| Column | Notes |
| --- | --- |
| `id` (uuid) | Primary key. |
| `workflow_id` (string) | Links to the owning Temporal Workflow ID. |
| `provider` | Enum (`none`, …) — external relay identifiers when configured. |
| `status` | Enum per state machine. |
| `created_at`, `ready_at`, `ended_at` | Lifecycle timestamps. |
| `expires_at` | TTL for automatic revocation. |
| `worker_identity` | Provenance for audit/support (Temporal worker ID). |
| `live_session_name`, `live_session_socket_path` | Worker-local identity when a relay is in use (DB column names may differ historically). |
| `attach_ro` | Plain RO attach string (safe to display). |
| `attach_rw_encrypted` | RW attach string encrypted at rest. |
| `web_ro`, `web_rw_encrypted` | Optional web-view URLs (RW encrypted). |
| `last_heartbeat_at` | Updated by worker while session is alive. |
| `error_message` | Optional field for failure diagnostics. |

### 7.2 `system_control_events`

Tracks operator-driven actions for audit.

| Column | Notes |
| --- | --- |
| `id` | Primary key. |
| `workflow_id` | Owning workflow. |
| `actor_user_id` | Operator identity. |
| `action` | `pause`, `resume`, `grant_rw`, `revoke_rw`, `create_session`, `revoke_session`, `send_message`, etc. |
| `created_at` | Timestamp. |
| `metadata_json` | Structured payload (e.g., TTL, reason, message body). |

---

## 8. API Surface & Temporal Signals

### 8.1 Session Lifecycle (REST APIs)

- `POST /api/workflows/{id}/live-session`: Idempotent creation/enabling.
- `GET /api/workflows/{id}/live-session`: Fetch status and RO attach info (including `web_ro` for log tailing).
- `POST /api/workflows/{id}/live-session/grant-write`: Returns time-limited RW endpoint or short-lived reveal token; logs audit event.
- `POST /api/workflows/{id}/live-session/revoke`: Forces worker teardown and updates status.

### 8.2 Control Actions (Temporal Signals)

Pauses and takeovers use standard **Temporal Signals**:
- Signal `pause_workflow`: The workflow blocks taking new actions at the next safe checkpoint.
- Signal `resume_workflow`: Removes the block.

### 8.3 Operator Messages

- `POST /api/workflows/{id}/operator-messages` with `{message: string}`. Persists to DB and streams into the workspace inbox.

---

## 9. Security Model

- RO endpoint is shown to authorized viewers by default. RW endpoint is withheld until an operator with permission explicitly requests it.
- RW grants are time-bound (default: 15 minutes) and logged in `system_control_events` with actor identity and TTL metadata.
- Revocation options:
  - **Revoke write**: Stop revealing RW; optionally restart session to rotate credentials.
  - **Revoke session**: Tear down the live session record and revoke worker-advertised endpoints.

---

## 10. Dashboard Integration

### 10.1 Task Detail Page

The task detail page (`/tasks/:taskId`) should include two live-session UI elements:

1. **Live Output Panel** (section 5): Collapsible panel with live log tailing. Toggle on/off. Available when log data exists for the run.
2. **Live Session Card** (section 6): Shows session status, RO attach instructions, optional web view link, pause/resume signal buttons, RW grant UI, and operator message composer. Available for full handoff workflows.

### 10.2 Feature Flags

```json
{
  "features": {
    "liveTaskManagement": {
      "logTailingEnabled": true,
      "handoffEnabled": false,
      "operatorMessagesEnabled": false
    }
  }
}
```

**Desired experience:** operators get artifact-backed log tailing in the task detail UI, optional live handoff controls (RO/RW, pause/resume, operator messages) when session metadata exists, and post-session artifacts such as **`transcript.log`**. Sequencing of UI/backend pieces is tracked in [`docs/tmp/remaining-work/Temporal-LiveTaskManagement.md`](../tmp/remaining-work/Temporal-LiveTaskManagement.md).

---

## 11. Open Questions

1. Should optional `web_ro` links (when present) open in a new tab, or should all terminal rendering stay inside first-party log components?
2. Should log tailing be enabled by default for all running tasks, or only when a live session has been explicitly provisioned?
3. Should completed workflow detail pages show the last captured terminal snapshot as a static artifact?
