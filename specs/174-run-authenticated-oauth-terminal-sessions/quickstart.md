# Quickstart

1. Start an OAuth session through `POST /api/v1/oauth-sessions`.
2. Poll `GET /api/v1/oauth-sessions/{session_id}` until `status` is `bridge_ready` or `awaiting_user`.
3. Attach Mission Control to `/ws/v1/terminal/{session_id}?token=<jwt>`.
4. Complete the provider login command in the terminal.
5. Finalize with `POST /api/v1/oauth-sessions/{session_id}/finalize`.
6. Verify the provider profile is registered and the auth runner has stopped.

Targeted verification:

```bash
python -m pytest tests/unit/auth/test_oauth_session_activities.py tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/api_service/api/test_oauth_terminal_websocket.py tests/integration/temporal/test_oauth_session.py -q
```
