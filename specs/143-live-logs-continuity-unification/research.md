# Research: Live Logs Continuity Unification

- The Phase 4 timeline viewer already consumes structured observability events and classifies publication and boundary rows separately, so Phase 5 can stay focused on row affordances instead of changing the viewer model again.
- The managed-session supervisor emits specific event metadata keys:
  - `summaryRef`
  - `checkpointRef`
  - `controlEventRef`
  - `resetBoundaryRef`
- Historical synthesis in `api_service/api/routers/task_runs.py` currently falls back to generic `artifactRef` metadata for older runs; aligning that path keeps UI behavior truthful across migrated and non-migrated histories.
- The current `Session Continuity` panel already exposes grouped runtime/continuity/control artifacts and latest refs, so Phase 5 only needs copy/label clarification rather than a new drill-down API.
