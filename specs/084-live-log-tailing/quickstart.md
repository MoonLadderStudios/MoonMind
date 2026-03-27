# Quickstart: Live Log Tailing

**Branch**: `084-live-log-tailing` | **Date**: 2026-03-17

## Prerequisites

- Docker Compose stack running (`docker compose up -d`)
- A managed agent task that provisions a tmate session (any supported managed CLI runtime, e.g. Gemini, Claude, Cursor, or Codex)

## Steps

1. Start the stack:
   ```bash
   docker compose up -d
   ```

2. Create a task that uses a managed agent runtime (the session is provisioned automatically by the sandbox worker).

3. Navigate to Mission Control → Tasks → click the running task to open its detail page.

4. Locate the **Live Output** panel (below the header, above the live session card).

5. Click the toggle to expand the panel. You should see:
   - If session is READY: a live terminal view showing agent output.
   - If session is STARTING: a loading indicator.
   - If no session: "Live output is not available for this task."

6. To verify disconnect behavior:
   - Collapse the panel — iframe should be removed (check devtools Network tab).
   - Switch to another browser tab — stream should pause.
   - Return to the tab — stream should resume automatically.

## Feature Flag

Set `MOONMIND_LOG_TAILING_ENABLED=false` in `.env` to disable the panel entirely. Default is `true`.
