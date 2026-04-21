# Data Model: Show Recent Manifest Runs

## ManifestRunRow

Represents one row returned by the existing execution history endpoint for `entry=manifest`.

Fields:

- `taskId` (required string): stable run/workflow identifier and row key.
- `source` (required string): backend source label fallback.
- `sourceLabel` (optional string): human-readable source fallback.
- `title` (optional string): run title fallback for manifest label.
- `manifestName` (optional string): registry or inline manifest label when present.
- `action` (optional string): submitted manifest action, such as `run` or `plan`.
- `status` (required string): current run status.
- `state`, `rawState`, `temporalStatus` (optional strings): alternate status fields from execution history.
- `currentStage`, `manifestStage`, `stage` (optional strings): current manifest stage when available.
- `startedAt`, `createdAt` (optional strings): timestamp sources for started time.
- `durationSeconds` (optional number): elapsed or total duration in seconds when available.
- `detailHref`, `link` (optional strings): direct detail URL when returned by the API.

Validation rules:

- Missing optional display fields render as `-`.
- Manifest label fallback order is `manifestName`, `sourceLabel`, `title`, `source`, `taskId`.
- Detail link fallback is `/tasks/{taskId}?source=temporal`.
- Stage detail is appended only when status is active and stage is non-empty.

## RecentRunFilters

Client-side filter state for the returned manifest rows.

Fields:

- `status` (string): `all` or exact status value.
- `manifest` (string): case-insensitive substring match against manifest label.
- `search` (string): case-insensitive substring match across run ID, manifest label, action, status, and stage.

Validation rules:

- Empty filter values do not remove rows.
- Filtering is local to the already-fetched 200-row result set.
