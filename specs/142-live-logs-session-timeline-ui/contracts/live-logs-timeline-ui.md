# Contract: Live Logs Session Timeline UI

## Summary

The Phase 4 UI consumes the existing task-run observability APIs and changes only the browser-side rendering and lifecycle behavior.

## Browser Contract

1. The panel must fetch `/observability-summary` before loading history.
2. If the session timeline feature flag is enabled, the panel should request `/observability/events` first.
3. If structured history is unavailable, the panel should request `/logs/merged` and render compatibility rows.
4. SSE follow mode remains on `/logs/stream` and must append the same canonical event shape used by structured history.
5. `session_reset_boundary` rows render as explicit timeline banners.
6. Session snapshot header fields may come from summary, history payload, or the newest event carrying session metadata.
7. When the session timeline feature flag is disabled, the panel may continue rendering the legacy line-oriented viewer while preserving the same fetch and live-connection lifecycle.
