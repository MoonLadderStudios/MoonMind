# Live Task Handoff via tmux + tmate

Status: Draft  
Owners: MoonMind Engineering  
Last Updated: 2026-02-18

---

## 1. Problem Statement

MoonMind tasks often run in "headless" worker shells where only buffered logs are visible. Operators cannot watch the live terminal, interject with clarifications, or take over without restaging the run. Codex Cloud lacks a native live-handoff mechanism, which makes it hard to observe stuck agents, unblock them quickly, or provide real-time guidance without duplicating the workspace elsewhere. We need a terminal-first solution that lets humans attach to the in-flight environment, communicate with the agent, and take control safely.

## 2. Goals

1. Provide true live observation of the terminal driving each TaskRun.
2. Allow lightweight operator messages for clarifications without pausing execution.
3. Offer an explicit, auditable handoff path with pause/resume controls and optional write access.
4. Minimize new surface area: rely on tmux-compatible semantics instead of a full IDE.
5. Default to secure behavior: read-only first, time-bound write grants, revocation, and audit trails.

## 3. Non-Goals

- Replacing existing artifact logs or workflow history; this feature is additive.
- Providing a collaborative web IDE experience (code-server, VS Code, etc.).
- Solving agent correctness; this is about enabling human interventions.

## 4. Concept Overview

Every TaskRun executes inside a dedicated `tmate` session (tmux-compatible). The worker owns the session, exposes read-only (RO) endpoints immediately, and keeps the read-write (RW) endpoints encrypted until an operator explicitly requests access. The MoonMind dashboard acts as the broker for attach links, pause/resume, and audit events. Because `tmate` mirrors tmux semantics, workers can define deterministic pane layouts and optionally surface web-based viewers when needed.

## 5. Architecture

### 5.1 Components

- **Orchestrator API + Dashboard**: Creates and manages live sessions, surfaces RO links, mediates RW grant + TTL, exposes pause/resume and operator messaging UI, and records audit events.
- **Worker Runtime**: Starts each TaskRun inside a tmate session, publishes endpoints and status, enforces pause/resume, handles operator inbox fan-out, and tears down sessions on completion.

### 5.2 Execution Model

- One tmate socket per run, e.g., `/tmp/moonmind/tmate/<run_id>.sock`.
- One session name per run, e.g., `mm-<run_id>`.
- Recommended pane layout:
  - Pane 0: agent runtime (Codex/Gemini/Claude CLI driver).
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
- **ENDED**: task completed; worker tore down the session normally.
- **ERROR**: tmate setup failed; the task keeps running headless.

## 6. Persistence Model

### 6.1 `task_run_live_sessions`

| Column | Notes |
| --- | --- |
| `id` (uuid) | Primary key. |
| `task_run_id` (fk) | Links to the owning TaskRun. |
| `provider` | Enum, initially `tmate`. |
| `status` | Enum per state machine. |
| `created_at`, `ready_at`, `ended_at` | Lifecycle timestamps. |
| `expires_at` | TTL for automatic revocation. |
| `worker_id`, `worker_hostname` | Provenance for audit/support. |
| `tmate_session_name`, `tmate_socket_path` | Socket path is worker-local only. |
| `attach_ro` | Plain RO attach string (safe to display). |
| `attach_rw_encrypted` | RW attach string encrypted at rest. |
| `web_ro`, `web_rw_encrypted` | Optional web-view URLs (RW encrypted). |
| `last_heartbeat_at` | Updated by worker while session is alive. |
| `error_message` | Optional field for failure diagnostics. |

### 6.2 `task_run_control_events`

Tracks operator-driven actions for audit.

| Column | Notes |
| --- | --- |
| `id` | Primary key. |
| `task_run_id` | Owning run. |
| `actor_user_id` | Operator identity. |
| `action` | `pause`, `resume`, `grant_rw`, `revoke_rw`, `create_session`, `revoke_session`, `send_message`, etc. |
| `created_at` | Timestamp. |
| `metadata_json` | Structured payload (e.g., TTL, reason, message body). |

## 7. API Surface

### 7.1 Session Lifecycle

- `POST /api/task-runs/{id}/live-session`: idempotent creation/enabling.
- `GET /api/task-runs/{id}/live-session`: fetch status and RO attach info.
- `POST /api/task-runs/{id}/live-session/grant-write`: returns time-limited RW endpoint or short-lived reveal token; logs audit event.
- `POST /api/task-runs/{id}/live-session/revoke`: forces worker teardown and updates status.

### 7.2 Control Actions

- `POST /api/task-runs/{id}/control` with `{action: "pause" | "resume" | "takeover"}`. The worker pauses new agent steps for soft pause, or escalates to hard pause semantics when necessary.

### 7.3 Operator Messages

- `POST /api/task-runs/{id}/operator-messages` with `{message: string}`. Persists to DB and streams into the workspace inbox so attached operators or the agent wrapper can react.

## 8. Worker Lifecycle

### 8.1 Image Requirements

Install `tmate`, `tmux` (optional because tmate embeds tmux), `openssh-client` (tmate depends on outbound SSH), and `ca-certificates`.

### 8.2 Directory Layout

Each run keeps live-session artifacts under the workflow artifact root (`var/artifacts/spec_workflows/<run_id>/` by default, or the configured artifact directory), including:

- `operator_inbox.jsonl` — append-only JSON lines for dashboard messages.
- `transcript.log` — optional tmux transcript capture.
- `control.json` — optional local control channel for pause flags.

### 8.3 Session Bootstrap

```bash
RUN_ID="$MOONMIND_TASK_RUN_ID"
SOCK_DIR="/tmp/moonmind/tmate"
SOCK="$SOCK_DIR/${RUN_ID}.sock"
SESSION="mm-${RUN_ID}"

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

moonmind_report_live_session \
  --run-id "$RUN_ID" \
  --status READY \
  --attach-ro "$SSH_RO" \
  --attach-rw "$SSH_RW" \
  --web-ro "$WEB_RO" \
  --web-rw "$WEB_RW"
```

Workers report endpoints once `tmate-ready` fires. RO endpoint is stored in plaintext; RW and optional web endpoints are encrypted before leaving the worker.

### 8.4 Heartbeat & TTL

Workers periodically PATCH `last_heartbeat_at` and refresh `expires_at` while viewers are present. If viewer detection is unreliable, the TTL simply counts down from creation and orchestrator revokes sessions when time elapses.

### 8.5 Teardown

On run completion or cancellation, workers call `tmate -S "$SOCK" kill-session -t "$SESSION"` (ignoring errors) and delete the socket before reporting `ENDED`. Revoke flows follow the same teardown path but use `REVOKED` as the terminal status.

## 9. Pause/Resume and Unstick Flow

- **Soft pause (default)**: the agent wrapper honors a pause flag (DB field or `control.json`) at safe checkpoints, prints `== PAUSED FOR OPERATOR ==`, and stops issuing new tool calls. The terminal remains live for RO viewers, and RW operators can use the dedicated pane without racing the agent.
- **Hard pause**: as a fallback, the worker sends `SIGSTOP` to the agent process group and `SIGCONT` on resume. Use only when the wrapper cannot reach a safe checkpoint quickly.
- **Operator takeover**: Dashboard workflow is pause → grant RW (15-minute TTL by default) → attach via RW pane → perform fixes/tests → enter an operator summary → resume. The summary is appended to task context for traceability.

## 10. Operator Messages

Operator messages provide a light-touch clarify channel without granting write access. Dashboard writes append JSON lines such as:

```json
{"ts":"2026-02-17T21:45:12Z","actor":"nathaniel","message":"Try reducing step size to 0.5 and rerun the unit test suite."}
```

Pane 2 tails `operator_inbox.jsonl`, so RO observers and the agent itself can see new guidance immediately. Agents may optionally parse the inbox to incorporate instructions at the next checkpoint.

## 11. Security Model

- RO endpoint is shown to authorized viewers by default. RW endpoint is withheld until an operator with permission explicitly requests it.
- RW grants are time-bound (e.g., 15 minutes) and logged in `task_run_control_events` with actor identity and TTL metadata.
- Revocation options:
  - **Revoke write**: stop revealing RW; optionally restart session to rotate credentials.
  - **Revoke session**: kill the tmate server and delete the socket.
- Sensitive output mitigation: avoid echoing secrets in scripts, continue masking tokens in logs, prefer ephemeral credentials, and consider self-hosted tmate relays for stricter network control.

## 12. Configuration

| Variable | Purpose |
| --- | --- |
| `MOONMIND_LIVE_SESSION_ENABLED_DEFAULT` | Toggle default enablement. |
| `MOONMIND_LIVE_SESSION_PROVIDER` | Currently `tmate`. |
| `MOONMIND_LIVE_SESSION_TTL_MINUTES` | Session lifetime before auto-revoke (default 60). |
| `MOONMIND_LIVE_SESSION_RW_GRANT_TTL_MINUTES` | Duration of RW reveals (default 15). |
| `MOONMIND_LIVE_SESSION_ALLOW_WEB` | Controls whether `tmate_web_*` links are surfaced. |
| `MOONMIND_TMATE_SERVER_HOST` | Optional override for self-hosted tmate relay. |
| `MOONMIND_LIVE_SESSION_MAX_CONCURRENT_PER_WORKER` | Backpressure control per worker. |

## 13. Failure Modes

- **tmate missing or cannot reach relay**: worker marks session `ERROR`; TaskRun continues without live handoff.
- **Worker restart**: session dies; worker reports `ENDED` or `ERROR` and optionally spins up a new session when the run restarts.
- **TTL expiration**: orchestrator calls revoke; worker tears down session while task continues headless.

## 14. Minimal Implementation Plan

1. Worker: build a `LiveSessionManager` that bootstraps tmate, manages panes, polls control flags, and reports endpoints.
2. Orchestrator/API: add `task_run_live_sessions` table, CRUD endpoints, and RW grant/revoke flows plus audit logging in `task_run_control_events`.
3. Dashboard: surface a Live Session card with RO attach instructions, optional web view, pause/resume buttons, RW grant UI with TTL indicator, and operator message composer.
4. Agent wrapper: add soft pause checkpoints and optional operator inbox ingestion so agents can respect pause/resume and integrate clarifications.
5. Audit & TTL: store control events, enforce TTL via background job, and ensure expired sessions trigger worker revocation hooks.

## 15. Optional Enhancements

- **Transcript capture** via `tmux pipe-pane` or `script` into `transcript.log` for post-mortems.
- **"Needs Operator" flag**: allow agents to request help, highlighting runs in the dashboard and prompting session enablement.
- **Self-hosted tmate relay**: improves availability and control over outbound SSH dependencies.
