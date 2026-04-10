# Quickstart: codex-managed-session-plane-phase11

## Goal

Verify that Mission Control can display and control a task-scoped Codex managed session without replacing the existing step-level logs and diagnostics panels.

## Steps

1. Start a Codex managed-session task that produces a task run ID and session projection.
2. Open the task detail page in Mission Control.
3. Confirm `Session Continuity` appears with:
   - the current session ID
   - the current epoch
   - latest summary/checkpoint/control/reset badges when available
4. Confirm the page still shows:
   - `Live Logs`
   - `Stdout`
   - `Stderr`
   - `Diagnostics`
5. Send a follow-up from the Session Continuity panel and confirm the panel refreshes successfully.
6. Trigger `Clear / Reset` and confirm the displayed epoch increments.
7. Trigger `Cancel` from the Session Continuity panel and confirm it uses the normal task cancellation flow.
8. Confirm no terminal attach, debug shell, or transcript explorer controls were added.
