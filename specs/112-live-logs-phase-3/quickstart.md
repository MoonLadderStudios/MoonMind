# Quickstart: Live Logs Phase 3

To test the Phase 3 backend SSE streaming:

1. Bring up MoonMind via docker compose:
   `docker compose up -d`
2. Enter the backend dev container and run the integration tests targeting `test_live_logs.py`:
   `tools/test_unit.sh tests/integration/api/test_live_logs.py`
3. Manually start a slow-running task. Send a curl request to observe the SSE event fan-out:
   `curl -N -H "Accept: text/event-stream" "http://localhost:8000/api/task-runs/<id>/logs/stream"`
4. Verify sequence numbers map continuously. Ensure disconnection (Ctrl+C) cleans up server-side references.
