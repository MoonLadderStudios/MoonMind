# Live Task Handoff via tmux + tmate

Status: Draft  
Owners: MoonMind Engineering  
Last Updated: 2026-03-14

---

## 1. Problem Statement

MoonMind Temporal Managed Agents often run in "headless" worker shells where only buffered logs are visible. Operators cannot watch the live terminal, interject with clarifications, or take over without restaging the run. Codex Cloud lacks a native live-handoff mechanism, which makes it hard to observe stuck agents, unblock them quickly, or provide real-time guidance without duplicating the workspace elsewhere. We need a terminal-first solution that lets humans attach to the in-flight environment, communicate with the agent, and take control safely.

## 2. Goals

1. Provide true live observation of the terminal driving each Temporal Workflow.
2. Allow lightweight operator messages for clarifications without pausing execution.
3. Offer an explicit, auditable handoff path with pause/resume controls and optional write access using Temporal Signals.
4. Minimize new surface area: rely on tmux-compatible semantics instead of a full IDE.
5. Default to secure behavior: read-only first, time-bound write grants, revocation, and audit trails.

## 3. Non-Goals

- Replacing existing artifact logs or workflow history; this feature is additive.
- Providing a collaborative web IDE experience (code-server, VS Code, etc.).
- Solving agent correctness; this is about enabling human interventions.

## 4. Concept Overview

Every Temporal Managed Agent run executes inside a dedicated `tmate` session (tmux-compatible). The sandbox worker owns the session, exposes read-only (RO) endpoints immediately, and keeps the read-write (RW) endpoints encrypted until an operator explicitly requests access. The MoonMind dashboard acts as the broker for attach links, pause/resume commands (via Temporal Signals), and audit events. Because `tmate` mirrors tmux semantics, workers can define deterministic pane layouts and optionally surface web-based viewers when needed.

## 5. Architecture

### 5.1 Components

- **Orchestrator API + Dashboard**: Creates and manages live sessions, surfaces RO links, mediates RW grant + TTL, sends pause/resume Temporal Signals, surfaces operator messaging UI, and records audit events.
- **Worker Runtime (Sandbox)**: Starts each Managed Agent inside a tmate session, publishes endpoints and status, honors Temporal Activity cancellation/pauses, handles operator inbox fan-out, and tears down sessions on completion.

### 5.2 Execution Model

- One tmate socket per workflow, e.g., `/tmp/moonmind/tmate/<workflow_id>.sock`.
- One session name per workflow, e.g., `mm-<workflow_id>`.
- Recommended pane layout:
  - Pane 0: agent runtime (OpenHands / Managed Agent core loop).
  - Pane 1: `tail -F` of structured task log.
  - Pane 2: `tail -F` of operator message inbox.
  - Pane 3: operator shell reserved for RW takeovers.
- Worker enforces `remain-on-exit` and high scrollback to preserve context.

### 5.3 Live Session State Machine

```
DISABLED → STARTING → READY → (REVOKED | ENDED | ERROR)
```

- **DISABLED**: no live session has been provisioned.
- **STARTING**: worker created the socket and is waiting for tmate endpoints.
- **READY**: RO endpoint is advertised; RW endpoint stored encrypted.
- **REVOKED**: orchestrator explicitly shut down the session.
- **ENDED**: workflow completed; worker tore down the session normally.
- **ERROR**: tmate setup failed; the agent keeps running headless.

## 6. Persistence Model

### 6.1 `workflow_live_sessions` (Database)

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

### 6.2 `system_control_events`

Tracks operator-driven actions for audit.

| Column | Notes |
| --- | --- |
| `id` | Primary key. |
| `workflow_id` | Owning workflow. |
| `actor_user_id` | Operator identity. |
| `action` | `pause`, `resume`, `grant_rw`, `revoke_rw`, `create_session`, `revoke_session`, `send_message`, etc. |
| `created_at` | Timestamp. |
| `metadata_json` | Structured payload (e.g., TTL, reason, message body). |

## 7. API Surface & Temporal Signals

### 7.1 Session Lifecycle (REST APIs)

- `POST /api/workflows/{id}/live-session`: idempotent creation/enabling.
- `GET /api/workflows/{id}/live-session`: fetch status and RO attach info.
- `POST /api/workflows/{id}/live-session/grant-write`: returns time-limited RW endpoint or short-lived reveal token; logs audit event.
- `POST /api/workflows/{id}/live-session/revoke`: forces worker teardown and updates status.

### 7.2 Control Actions (Temporal Signals)

In the Temporal architecture, pauses and takeovers are not REST API database toggles. The dashboard sends standard **Temporal Signals** to the workflow execution:
- Signal `pause_workflow`: The workflow receives this and blocks taking new actions (Agent loop is soft-paused).
- Signal `resume_workflow`: Removes the block.

### 7.3 Operator Messages

- `POST /api/workflows/{id}/operator-messages` with `{message: string}`. Persists to DB and streams into the workspace inbox so attached operators or the agent wrapper can react.

## 8. Worker Lifecycle

### 8.1 Image Requirements

Install `tmate`, `tmux` (optional because tmate embeds tmux), `openssh-client` (tmate depends on outbound SSH), and `ca-certificates`.

### 8.2 Directory Layout

Each run keeps live-session artifacts under the workflow workspace (`/work/agent_jobs/<workflow_id>/artifacts/`):

- `operator_inbox.jsonl` — append-only JSON lines for dashboard messages.
- `transcript.log` — optional tmux transcript capture.

### 8.3 Session Bootstrap

```bash
WORKFLOW_ID="$TEMPORAL_WORKFLOW_ID"
SOCK_DIR="/tmp/moonmind/tmate"
SOCK="$SOCK_DIR/${WORKFLOW_ID}.sock"
SESSION="mm-${WORKFLOW_ID}"

mkdir -p "$SOCK_DIR"

tmate -S "$SOCK" new-session -d -s "$SESSION"
tmate -S "$SOCK" set -g remain-on-exit on
tmate -S "$SOCK" set -g history-limit 200000

tmate -S "$SOCK" split-window -h -t "${SESSION}:0"
tmate -S "$SOCK" split-window -v -t "${SESSION}:0.0"
tmate -S "$SOCK" split-window -v -t "${SESSION}:0.1"

# Pane assignments: agent runtime, task log, operator inbox, operator shell.

tmate -S "$SOCK" wait tmate-ready

SSH_RO=$(tmate -S "$SOCK" display -p '#{tmate_ssh_ro}')
SSH_RW=$(tmate -S "$SOCK" display -p '#{tmate_ssh}')
WEB_RO=$(tmate -S "$SOCK" display -p '#{tmate_web_ro}')
WEB_RW=$(tmate -S "$SOCK" display -p '#{tmate_web}')

# The worker can then emit an Activity to register this session with the DB.
```

## 9. Pause/Resume and Unstick Flow

- **Soft pause (default)**: As implemented in the Managed Agent workflow, the agent wrapper honors a `pause_workflow` Temporal signal at safe checkpoints, prints `== PAUSED FOR OPERATOR ==`, and stops issuing new tool calls. The terminal remains live for RO viewers, and RW operators can use the dedicated pane without racing the agent.
- **Operator takeover**: Dashboard workflow is pause → grant RW (15-minute TTL by default) → attach via RW pane → perform fixes/tests → enter an operator summary → resume. The summary is appended to task context for traceability.

## 10. Operator Messages

Operator messages provide a light-touch clarify channel without granting write access. Dashboard writes append JSON lines such as:

```json
{"ts":"2026-03-14T21:45:12Z","actor":"nathaniel","message":"Try reducing step size to 0.5 and rerun the unit test suite."}
```

Pane 2 tails `operator_inbox.jsonl`, so RO observers and the agent itself can see new guidance immediately. Agents may optionally parse the inbox to incorporate instructions at the next checkpoint.

## 11. Security Model

- RO endpoint is shown to authorized viewers by default. RW endpoint is withheld until an operator with permission explicitly requests it.
- RW grants are time-bound (e.g., 15 minutes) and logged in `system_control_events` with actor identity and TTL metadata.
- Revocation options:
  - **Revoke write**: stop revealing RW; optionally restart session to rotate credentials.
  - **Revoke session**: kill the tmate server and delete the socket.

## 12. Minimal Implementation Plan

1. **Worker**: Build a `LiveSessionManager` script run inside the sandbox that bootstraps tmate, manages panes, and reports endpoints to Temporal via Activities.
2. **Orchestrator API**: Add `workflow_live_sessions` table, CRUD endpoints, and RW grant/revoke flows.
3. **Temporal Workflows**: Support Temporal signals (`pause_workflow`, `resume_workflow`) inside the agent loop.
4. **Dashboard**: Surface a Live Session card with RO attach instructions, optional web view, pause/resume signal buttons, RW grant UI, and operator message composer.
