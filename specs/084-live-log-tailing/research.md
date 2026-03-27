# Research: Live Log Tailing

**Branch**: `084-live-log-tailing` | **Date**: 2026-03-17

## R1: tmate Web RO Viewer Capabilities

**Decision**: Embed the tmate web RO URL directly in an iframe.

**Rationale**: The tmate web viewer is a fully functional terminal emulator that handles rendering, scrollback, ANSI escape codes, and connection management. It provides a native rolling buffer of terminal output and auto-reconnects on transient failures. No custom terminal widget (e.g., xterm.js) is needed for v1.

**Alternatives Considered**:
- **xterm.js + custom WebSocket**: More rendering control but requires a backend proxy to convert the tmate SSH stream to a WebSocket. Significant additional infrastructure for marginal v1 benefit.
- **Server-side capture to API**: Worker writes output to a ring-buffer file; a new endpoint serves it. More control but adds backend complexity and polling overhead.

## R2: Existing Live Session Infrastructure

**Decision**: Reuse the existing live session API and database schema without modification.

**Rationale**: The existing `GET /api/queue/jobs/{id}/live-session` endpoint already returns `web_ro` URLs. The `task_run_live_sessions` table already stores the `web_ro` column. The worker bootstrap already captures and registers the tmate web RO URL. No schema changes or new endpoints are needed.

**Key files confirmed**:
- `moonmind/schemas/agent_queue_models.py` — `web_ro` field on response models
- `moonmind/workflows/agent_queue/models.py` — `web_ro` DB column
- `moonmind/agents/codex_worker/worker.py` — managed agent queue worker (all runtimes); captures `#{tmate_web_ro}` during bootstrap
- `api_service/api/routers/agent_queue.py` — live session endpoints
- `api_service/api/routers/task_runs.py` — live session endpoints (Temporal path)

## R3: Feature Flag Mechanism

**Decision**: Add `logTailingEnabled` to the view model config returned to the dashboard.

**Rationale**: The dashboard view model (`task_dashboard_view_model.py`) already returns configuration objects. Adding a boolean flag is trivial and consistent with how other feature controls work. An environment variable `MOONMIND_LOG_TAILING_ENABLED` defaults to `true`.
