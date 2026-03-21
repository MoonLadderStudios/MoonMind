# Data Model: Task Editing System

## Entity: QueueJobEditabilitySnapshot

- **Description**: Server-side snapshot used to determine whether a queue job can be edited.
- **Fields**:
  - `id` (UUID)
  - `type` (string)
  - `status` (enum queue status)
  - `startedAt` (datetime | null)
  - `updatedAt` (datetime)
  - `createdByUserId` (UUID | null)
  - `requestedByUserId` (UUID | null)
- **Rules**:
  - Editable only when `type == "task"`, `status == "queued"`, and `startedAt == null`.
  - Actor must match owner semantics (`createdByUserId` or `requestedByUserId`).

## Entity: QueuedJobUpdateRequest

- **Description**: API payload for updating a queued task job in place.
- **Fields**:
  - `type` (string; must equal existing job type)
  - `priority` (integer)
  - `maxAttempts` (integer >= 1)
  - `affinityKey` (string | null)
  - `payload` (object; canonical task payload)
  - `expectedUpdatedAt` (datetime | null)
  - `note` (string | null, <=256 chars)
- **Rules**:
  - Payload contract mirrors create envelope to preserve UI serializer reuse.
  - `expectedUpdatedAt` mismatch yields conflict (`409`) without mutation.
  - `note` is audit metadata only (not persisted as a job column).

## Entity: QueueJobMutableUpdate

- **Description**: In-place queue-row mutation applied after validation.
- **Mutable Fields**:
  - `priority`
  - `payload`
  - `affinity_key`
  - `max_attempts`
  - `updated_at` (refreshed to transaction timestamp)
- **Rules**:
  - `id`, `type`, creation metadata, and lifecycle history remain immutable.
  - Mutation is transactional and occurs only after lock + invariants pass.

## Entity: JobUpdateAuditEvent

- **Description**: Queue event appended for successful updates.
- **Fields**:
  - `jobId` (UUID)
  - `level` (`info`)
  - `message` (`"Job updated"`)
  - `payload.actorUserId` (string UUID)
  - `payload.changedFields` (list[string])
  - `payload.note` (optional string)
  - `createdAt` (datetime)
- **Rules**:
  - Event must be appended in the same transaction as job mutation.
  - `changedFields` reflects only values changed by the update request.

## Entity: EditSessionState (Dashboard)

- **Description**: Client-side state for `/tasks/queue/new?editJobId=...` edit flow.
- **Fields**:
  - `editJobId` (normalized UUID string)
  - `expectedUpdatedAt` (string datetime from fetched job)
  - `isEditable` (boolean)
  - `cancelHref` (string, defaults to `/tasks/queue/{jobId}`)
  - `error` (string | null)
- **Rules**:
  - Invalid or non-editable jobs block submit and show recoverable navigation to queue/detail.
  - Update submit uses same create-envelope builder plus `expectedUpdatedAt` when set.

## Entity: QueueUpdateErrorEnvelope

- **Description**: Normalized API error payload used by dashboard for actionable messaging.
- **Fields**:
  - `code` (string)
  - `message` (string)
- **Codes in Scope**:
  - `job_not_found` (404)
  - `job_not_authorized` (403)
  - `job_state_conflict` (409)
  - `invalid_queue_payload` (422)

## State Transitions

- **Editable update**: `queued -> queued` (in-place mutation, same job ID).
- **Race conflict**: update rejected when worker transition has already made job `running` or set `startedAt`.
- **Optimistic conflict**: update rejected when `expectedUpdatedAt` does not equal current `updatedAt`.

## Concurrency Model

- Repository acquires row lock for target job before invariant checks.
- Worker claim path and update path rely on DB locking/state guards so only one operation wins.
- Conflict outcomes are explicit and non-destructive; no silent fallback mutation is allowed.
