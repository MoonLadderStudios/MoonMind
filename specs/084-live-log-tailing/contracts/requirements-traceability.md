# Requirements Traceability: Live Log Tailing

**Branch**: `084-live-log-tailing` | **Date**: 2026-03-17

| DOC-REQ | FR Mapping | Implementation Surface | Validation Strategy |
| --- | --- | --- | --- |
| DOC-REQ-001 | FR-001, FR-013 | `dashboard.js`: `renderLiveOutputPanel()` — collapsible panel with ~200-line rolling buffer via tmate web viewer | Manual: toggle panel on running task, verify terminal output appears |
| DOC-REQ-002 | FR-013 | tmate web viewer handles rolling buffer natively; iframe embed auto-streams | Manual: observe new lines arriving, old lines scrolling off |
| DOC-REQ-003 | FR-008, FR-009 | `dashboard.js`: `visibilitychange` listener + collapse handler removes iframe | Manual: collapse panel, verify no network activity; background tab, verify disconnect |
| DOC-REQ-004 | FR-002 | `dashboard.js`: iframe `src` set to `web_ro` URL from live session response | Manual: inspect iframe src matches `web_ro`; Unit: mock session with `web_ro` |
| DOC-REQ-005 | FR-003 | `dashboard.js`: panel default state is collapsed, no iframe created | Manual: load detail page, verify panel is collapsed; Unit: assert no iframe on initial render |
| DOC-REQ-006 | FR-004 | `dashboard.js`: show loading spinner when session status is `STARTING` | Manual: observe on provisioning task; Unit: mock STARTING state |
| DOC-REQ-007 | FR-005 | `dashboard.js`: render "Session ended" for ENDED/REVOKED status | Unit: mock ENDED state, assert message text |
| DOC-REQ-008 | FR-006 | `dashboard.js`: render "Live output is not available" for DISABLED/ERROR | Unit: mock DISABLED and ERROR states, assert message text |
| DOC-REQ-009 | FR-007 | Uses existing `GET /api/queue/jobs/{id}/live-session` and `GET /api/task-runs/{id}/live-session`; no new endpoint | Verify: no new route registrations in diff |
| DOC-REQ-010 | FR-011 | `task_dashboard_view_model.py`: `logTailingEnabled` flag; `dashboard.js`: conditional rendering | Unit: test flag controls panel visibility |
| DOC-REQ-011 | FR-012 | `dashboard.js`: switch on status value for all 6 states | Unit: render function tested with each status enum value |
| DOC-REQ-012 | FR-009, FR-010 | `dashboard.js`: `visibilitychange` event listener pauses/resumes iframe | Manual: tab switch test; Unit: mock visibilitychange event |
