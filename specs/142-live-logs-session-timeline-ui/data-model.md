# Data Model: Live Logs Session Timeline UI

## Frontend Timeline Row

The Phase 4 UI keeps the backend `RunObservabilityEvent` contract intact and projects it into a frontend row model:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | `string` | Stable React/Virtuoso key derived from sequence + split-line index |
| `text` | `string` | Visible row text after event splitting |
| `stream` | `stdout \| stderr \| system \| session \| unknown` | Preserves MoonMind stream provenance |
| `kind` | `string \| null` | Drives row treatment for lifecycle/publication/boundary rows |
| `sequence` | `number \| null` | Shared run-global ordering anchor |
| `timestamp` | `string \| null` | Event time for compact metadata display |
| `sessionId` | `string \| null` | Latest known bounded session identity from the event |
| `sessionEpoch` | `number \| null` | Epoch value used for boundary and header rendering |
| `containerId` | `string \| null` | Optional header/session context |
| `threadId` | `string \| null` | Optional header/session context |
| `turnId` | `string \| null` | Optional row/session context |
| `activeTurnId` | `string \| null` | Optional row/session context |
| `metadata` | `Record<string, unknown>` | Optional row-specific payload for later drill-down |
| `rowType` | `output \| system \| session \| approval \| publication \| boundary \| fallback` | Frontend rendering bucket |

## Header Snapshot Precedence

The panel header should use the newest bounded session snapshot available in this order:

1. `/observability-summary.sessionSnapshot`
2. `/observability/events.sessionSnapshot`
3. newest timeline row carrying session fields

## Fallback Semantics

| Source | When used | Notes |
| --- | --- | --- |
| Structured history | Preferred | Canonical initial-load source |
| Merged artifact text | When structured history is unavailable | Converted to `fallback` rows |
| SSE | After initial content only | Appends canonical event rows for active runs |

## Feature Flag Semantics

| Flag | Effect |
| --- | --- |
| `logStreamingEnabled` | Transport availability for SSE/live follow |
| `liveLogsSessionTimelineEnabled` | Enables the richer timeline/header/viewer path; when `false`, the legacy line viewer remains active |
