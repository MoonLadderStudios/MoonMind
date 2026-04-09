# Contract: Live Logs Continuity Unification

## Observability Event Metadata

The Phase 5 UI depends on these metadata keys when present on session timeline rows:

- `summaryRef`
- `checkpointRef`
- `controlEventRef`
- `resetBoundaryRef`
- `artifactRef` as a generic fallback

Historical synthesis must preserve the specific ref keys for publication and clear/reset rows so the same frontend rendering works for journal-backed and synthesized histories.

## UI Contract

- Live Logs rows may render zero or more inline artifact links beneath the main row text.
- Session Continuity remains the grouped artifact drill-down surface and does not become the source of main timeline row content.
