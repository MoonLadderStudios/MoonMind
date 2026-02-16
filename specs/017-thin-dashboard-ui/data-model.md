# Data Model: Thin Dashboard Task UI

## DashboardRun

Normalized row model used by consolidated and source list views.

| Field | Type | Description |
|------|------|-------------|
| `source` | enum (`queue` \| `speckit` \| `orchestrator`) | Origin system for the record |
| `id` | string | Source-specific run/job identifier |
| `displayName` | string | Human-friendly title derived from source payload |
| `normalizedStatus` | enum (`queued` \| `running` \| `awaiting_action` \| `succeeded` \| `failed` \| `cancelled`) | Common display status |
| `rawStatus` | string | Original source status value |
| `createdAt` | string \| null | Creation timestamp |
| `startedAt` | string \| null | Start timestamp |
| `finishedAt` | string \| null | Completion timestamp |
| `link` | string | Route to the source detail page |

### Status Mapping Rules

- Queue: `queued -> queued`, `running -> running`, `succeeded -> succeeded`, `failed|dead_letter -> failed`, `cancelled -> cancelled`.
- SpecKit: `pending|retrying -> queued`, `running -> running`, `succeeded|no_work -> succeeded`, `failed -> failed`, `cancelled -> cancelled`.
- Orchestrator: `pending -> queued`, `running -> running`, `awaiting_approval -> awaiting_action`, `succeeded|rolled_back -> succeeded`, `failed -> failed`.

## SubmitFormState

Tracks submit interactions per source form.

| Field | Type | Description |
|------|------|-------------|
| `kind` | enum (`queue` \| `speckit` \| `orchestrator`) | Form source |
| `values` | object | Current editable fields |
| `isSubmitting` | boolean | In-flight state |
| `errorMessage` | string \| null | Last submit error |
| `successId` | string \| null | Created run/job identifier |

## SourceHealthState

Per-source health state for polling and error boundaries.

| Field | Type | Description |
|------|------|-------------|
| `source` | enum (`queue` \| `speckit` \| `orchestrator`) | API source |
| `lastSuccessAt` | string \| null | Last successful refresh timestamp |
| `lastErrorAt` | string \| null | Last failed refresh timestamp |
| `errorMessage` | string \| null | Current visible error |

## DetailViewState

Shared state shape for detail pages.

| Field | Type | Description |
|------|------|-------------|
| `record` | object \| null | Source detail response |
| `eventsOrTasks` | array | Queue events or workflow/orchestrator step/task timeline |
| `artifacts` | array | Artifact metadata entries |
| `loading` | boolean | Detail request in-flight |
| `error` | string \| null | Visible error message |

## Polling Lifecycle

1. Initial page load seeds source states as loading.
2. Poll cycle updates each source independently.
3. Failed source updates only that source's `SourceHealthState` and preserves prior successful render state.
4. Document hidden state pauses timers and resumes on visibility restoration.
