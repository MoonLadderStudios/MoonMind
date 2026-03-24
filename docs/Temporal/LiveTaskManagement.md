# Live Task Management

Status: Draft  
Owners: MoonMind Engineering  
Last Updated: 2026-03-24

---

## 1. Purpose

Operators need real-time visibility into running tasks. This document defines two complementary capabilities that share the same underlying session infrastructure:

1. **Live Log Tailing** — An on-demand, read-only view of the most recent terminal output from a running task, rendered directly in the Mission Control task detail page.
2. **Live Terminal Handoff** — Full interactive terminal access to a running task's environment, allowing operators to observe, communicate with, or take over agent execution.

Both capabilities are backed by a per-workflow `tmate` session (tmux-compatible). Log tailing requires only the read-only (RO) endpoint; terminal handoff extends to read-write (RW) access with explicit grants.

---

## 2. Goals

1. Show operators what the agent is doing right now — without requiring a separate terminal client.
2. Provide a rolling window of recent terminal output (last ~200 lines) in the Mission Control UI.
3. Allow operators to escalate from passive observation to active intervention when needed.
4. Default to secure behavior: read-only first, time-bound write grants, revocation, and audit trails.
5. Minimize new surface area: rely on tmux-compatible semantics, not a full IDE.

---

## 3. Non-Goals

- Replacing artifact logs or workflow history; these capabilities are additive.
- Providing a collaborative web IDE experience (code-server, VS Code, etc.).
- Solving agent correctness; this is about enabling human observation and intervention.

---

## 4. Shared Infrastructure: tmate Sessions

Both capabilities depend on a single `tmate` session per workflow execution.

> [!NOTE]
> The tmate session lifecycle, shared `TmateSessionManager` abstraction, self-hosted server configuration, and endpoint persistence model are defined in [TmateSessionArchitecture.md](TmateSessionArchitecture.md). This section describes the conceptual integration points; see that document for implementation details.

### 4.1 Session Lifecycle

Every Temporal Managed Agent run executes inside a dedicated `tmate` session. The `agent_runtime` activity worker owns the session, exposes the RO endpoint immediately, and keeps the RW endpoint encrypted until an operator explicitly requests access.

```
DISABLED → STARTING → READY → (REVOKED | ENDED | ERROR)
```

- **DISABLED**: No live session has been provisioned.
- **STARTING**: Worker created the socket and is waiting for tmate endpoints.
- **READY**: RO endpoint is advertised; RW endpoint stored encrypted.
- **REVOKED**: system explicitly shut down the session.
- **ENDED**: Workflow completed; worker tore down the session normally.
- **ERROR**: tmate setup failed; the agent keeps running headless.

### 4.2 Session Bootstrap (ManagedRuntimeLauncher)

> [!NOTE]
> The description below is **target architecture**. Currently, `ManagedRuntimeLauncher.launch()` contains inline tmate lifecycle logic (~100 lines) that handles socket creation, config file generation, readiness waiting, and endpoint extraction directly. The target state refactors this into the shared `TmateSessionManager` (see [TmateSessionArchitecture.md](TmateSessionArchitecture.md) §4).

In the target architecture, the launcher delegates tmate concerns to `TmateSessionManager`:

1. **Launcher wrapping**: When `TmateSessionManager.is_available()` returns true, the launcher creates a `TmateSessionManager` instance and calls `start()` with the agent command. The manager handles socket creation, config file generation (including self-hosted server settings), readiness detection, and endpoint extraction.
2. **Endpoint persistence**: After `start()` returns `TmateEndpoints`, the launcher (or supervisor callback) invokes the `agent_runtime.report_live_session` activity to persist endpoints to the `workflow_live_sessions` table.
3. **UI embedding**: The Mission Control task detail page fetches `GET /api/workflows/{id}/live-session` and embeds the `web_ro` URL in the Live Output panel.
4. **Teardown**: The supervisor calls `TmateSessionManager.teardown()` on process completion, and the `agent_runtime.end_live_session` activity transitions the session status to `ENDED`.

```bash
# Conceptual wrapper executed by ManagedRuntimeLauncher
WORKFLOW_ID="mm-run-${RUN_ID}"
SOCK_DIR="/tmp/moonmind/tmate"
SOCK="$SOCK_DIR/${WORKFLOW_ID}.sock"

mkdir -p "$SOCK_DIR"

# Start the agent runtime wrapped in tmate
tmate -S "$SOCK" new-session -d -s "$WORKFLOW_ID" "gemini --yolo --prompt ..."
tmate -S "$SOCK" set -g remain-on-exit on
tmate -S "$SOCK" set -g history-limit 200000

tmate -S "$SOCK" wait tmate-ready

# Launcher extracts endpoints to save to DB
SSH_RO=$(tmate -S "$SOCK" display -p '#{tmate_ssh_ro}')
SSH_RW=$(tmate -S "$SOCK" display -p '#{tmate_ssh}')
WEB_RO=$(tmate -S "$SOCK" display -p '#{tmate_web_ro}')
WEB_RW=$(tmate -S "$SOCK" display -p '#{tmate_web}')
```

### 4.3 Recommended Pane Layout

- **Pane 0**: Agent runtime (managed agent core loop).
- **Pane 1**: `tail -F` of structured task log.
- **Pane 2**: `tail -F` of operator message inbox.
- **Pane 3**: Operator shell (reserved for RW takeovers).

### 4.4 Image Requirements

Install `tmate`, `tmux` (optional — tmate embeds tmux), `openssh-client` (tmate depends on outbound SSH), and `ca-certificates`.

### 4.5 Directory Layout

Each run keeps live-session artifacts under the workflow workspace (`/work/agent_jobs/<workflow_id>/artifacts/`):

- `operator_inbox.jsonl` — Append-only JSON lines for dashboard messages.
- `transcript.log` — Optional tmux transcript capture.

---

## 5. Live Log Tailing

### 5.1 Concept

The Mission Control task detail page includes a collapsible **Live Output** panel. When the operator toggles it open:

1. The UI fetches the most recent ~200 lines of terminal output from the running session.
2. New lines stream in continuously, pushing older lines off the top of the buffer.
3. The operator sees a live tail of the agent's terminal — like `tail -f` in the browser.

When the panel is collapsed or the tab is backgrounded, polling stops.

### 5.2 Data Source

The tmate session's **web RO endpoint** provides a browser-accessible read-only terminal view. The UI embeds the `web_ro` URL directly in the detail page — the browser connects to tmate's web viewer with zero additional backend work. The tmate web viewer handles terminal rendering, scrollback, and the rolling buffer natively.

### 5.3 UI Behavior

- **Default state**: Panel collapsed, no connection established.
- **On toggle open**: Load the web RO terminal view. Show a loading indicator while the session connects.
- **While open**: Terminal output streams continuously. The tmate web viewer handles rendering, scrollback, and the rolling buffer natively.
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
  "web_ro": "https://tmate.io/t/ro-xxxxxxxxxxxx",
  "attach_ro": "ssh ro-xxxxxxxxxxxx@nyc1.tmate.io"
}
```

The UI uses `web_ro` for the embedded terminal viewer. No new endpoint is needed for v1.

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
| `provider` | Enum, initially `tmate`. |
| `status` | Enum per state machine. |
| `created_at`, `ready_at`, `ended_at` | Lifecycle timestamps. |
| `expires_at` | TTL for automatic revocation. |
| `worker_identity` | Provenance for audit/support (Temporal worker ID). |
| `tmate_session_name`, `tmate_socket_path` | Socket path is worker-local only. |
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
  - **Revoke session**: Kill the tmate server and delete the socket.

---

## 10. Dashboard Integration

### 10.1 Task Detail Page

The task detail page (`/tasks/:taskId`) should include two live-session UI elements:

1. **Live Output Panel** (§5): Collapsible panel with embedded web RO terminal view. Toggle on/off. Available whenever the live session is `READY`.
2. **Live Session Card** (§6): Shows session status, RO attach instructions, optional web view link, pause/resume signal buttons, RW grant UI, and operator message composer. Available for full handoff workflows.

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

Log tailing should ship first as it requires only the RO endpoint and no new backend infrastructure beyond the existing live session system.

---

## 11. Rollout Plan

### Phase 1: Live Log Tailing

- Ensure live sessions provision the `web_ro` URL for all managed agent runs.
  - **Status**: `TmateSessionManager` extracts endpoints in `launcher.py`. Endpoint persistence via `workflow_live_sessions` table and `GET /api/workflows/{id}/live-session` API is designed (see [TmateSessionArchitecture.md](TmateSessionArchitecture.md) §5) but not yet implemented.
- Add the collapsible Live Output panel to the task detail page.
- Embed the tmate web RO viewer when the operator toggles it open.
- Handle session lifecycle states (loading, ready, ended, error, not available).

### Phase 2: Live Terminal Handoff

- Add the Live Session Card with RO attach info and status.
- Enable pause/resume signal buttons.
- Enable RW grant/revoke flows with time-bound TTL.
- Add operator message composer.

### Phase 3: Post-Session Artifacts

- Capture `transcript.log` as a downloadable artifact on session end.
- Show transcript link on the detail page for completed workflows.

---

## 12. Open Questions

1. Should `web_ro` embedding use a raw iframe or a terminal widget library (e.g., xterm.js) that connects to the tmate web RO stream?
2. Should log tailing be enabled by default for all running tasks, or only when a live session has been explicitly provisioned?
3. Should completed workflow detail pages show the last captured terminal snapshot as a static artifact?
