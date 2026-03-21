# Data Model: Resubmit Terminal Tasks

## Entity: QueuePrefillEligibilitySnapshot

- **Description**: Server-returned queue job snapshot used to determine whether prefill mode is create/edit/resubmit.
- **Fields**:
  - `id` (UUID)
  - `type` (string)
  - `status` (enum queue status)
  - `startedAt` (datetime | null)
  - `updatedAt` (datetime)
  - `createdByUserId` (UUID | null)
  - `requestedByUserId` (UUID | null)
- **Rules**:
  - Edit eligible when `type == "task"`, `status == "queued"`, and `startedAt == null`.
  - Resubmit eligible when `type == "task"` and `status in {"failed", "cancelled"}`.
  - Any other combination is prefill-ineligible.

## Entity: ResubmitJobRequest

- **Description**: API request envelope for creating a replacement job from a terminal source.
- **Fields**:
  - `type` (string; must equal source job type, v1 requires `task`)
  - `priority` (integer; default `0`)
  - `maxAttempts` (integer >= 1; default `3`)
  - `affinityKey` (string | null)
  - `payload` (object; canonical task payload)
  - `note` (string | null, <=256 chars)
- **Rules**:
  - Request mirrors create/update envelope shape for submit-builder reuse.
  - Payload must pass canonical task normalization/runtime-gate checks.
  - Attachment mutation fields are invalid in v1 resubmit.

## Entity: ResubmittedJob

- **Description**: Newly created queued job produced by successful resubmit.
- **Fields**:
  - `id` (UUID, new identifier)
  - `type` (`task`)
  - `status` (`queued`)
  - `payload` (normalized task payload copy)
  - `priority`, `maxAttempts`, `affinityKey`
  - `createdByUserId`, `requestedByUserId` (actor or inherited fallback)
  - `createdAt`, `updatedAt`
- **Rules**:
  - Source job row remains unchanged.
  - New job follows standard create lifecycle (`Job queued` event + normal queue processing).

## Entity: ResubmitAuditEvent

- **Description**: Source-job audit entry for successful resubmission.
- **Fields**:
  - `jobId` (source UUID)
  - `level` (`info`)
  - `message` (`"Job resubmitted"`)
  - `payload.newJobId` (string UUID)
  - `payload.actorUserId` (string UUID | null)
  - `payload.changedFields` (array[string])
  - `payload.note` (optional string)
  - `createdAt` (datetime)
- **Rules**:
  - Must be persisted in the same transaction as new-job creation.
  - `changedFields` captures request differences relative to source snapshot.

## Entity: ResubmittedFromAuditEvent

- **Description**: New-job lineage event referencing the source job.
- **Fields**:
  - `jobId` (new UUID)
  - `level` (`info`)
  - `message` (`"Job resubmitted from"`)
  - `payload.sourceJobId` (string UUID)
  - `createdAt` (datetime)
- **Rules**:
  - Present on successful resubmit to support reverse lineage lookup.

## Entity: QueueFormModeState

- **Description**: Dashboard state used by `/tasks/queue/new?editJobId=<jobId>` prefill flow.
- **Fields**:
  - `sourceJobId` (UUID string)
  - `mode` (`create` | `edit` | `resubmit`)
  - `expectedUpdatedAt` (datetime string; edit-only optimistic token)
  - `submitLabel` (`Create` | `Update` | `Resubmit`)
  - `cancelHref` (detail route for source/new context)
  - `attachmentNotice` (string | null; non-null in resubmit mode)
- **Rules**:
  - Mode is resolved from fetched source snapshot; URL param alone does not force mode.
  - Resubmit mode uses source ID for endpoint path and redirects to new job detail on success.

## Entity: QueueResubmitErrorEnvelope

- **Description**: Normalized API error payload consumed by dashboard resubmit handling.
- **Fields**:
  - `code` (string)
  - `message` (string)
- **Codes in Scope**:
  - `job_not_found` (404)
  - `job_not_authorized` (403)
  - `job_state_conflict` (409)
  - `invalid_queue_payload` (422)
  - `queue_internal_error` (500)

## State Transitions

- **Edit path (existing)**: `queued -> queued` (same job ID, in-place mutation).
- **Resubmit path (new)**:
  - Source: `failed/cancelled` remains unchanged.
  - New job: created directly in `queued`.
- **Ineligible path**: non-terminal or non-task sources reject with no mutation and no new job.

## Concurrency Model

- Source state is revalidated at submit time; stale UI prefill cannot bypass eligibility checks.
- Resubmit creates a distinct job record transactionally with lineage events.
- If source state is no longer eligible at submit time, service returns deterministic conflict.
