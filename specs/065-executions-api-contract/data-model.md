# Data Model: Executions API Contract Runtime Delivery

## Entity: ExecutionIdentity

- **Description**: Durable and per-run identifiers for a Temporal-backed MoonMind execution.
- **Fields**:
  - `workflowId` (string, durable canonical identifier)
  - `runId` (string, current run instance identifier)
  - `namespace` (string)
  - `workflowType` (enum: `MoonMind.Run` | `MoonMind.ManifestIngest`)
- **Rules**:
  - `workflowId` is the only path key and durable API handle.
  - `runId` may change across Continue-As-New or rerun flows.
  - `taskId` is not part of this API entity.

## Entity: ExecutionProjectionRecord

- **Description**: Current internal materialized row that backs execution list/detail/control behavior.
- **Fields**:
  - `workflowId` (string, PK)
  - `runId` (string)
  - `namespace` (string)
  - `workflowType` (enum)
  - `ownerId` (string nullable)
  - `state` (enum: `initializing|planning|executing|awaiting_external|finalizing|succeeded|failed|canceled`)
  - `closeStatus` (enum nullable: `completed|failed|canceled|terminated|timed_out|continued_as_new`)
  - `entry` (enum/string: `run|manifest`)
  - `searchAttributes` (object)
  - `memo` (object)
  - `artifactRefs` (array[string])
  - `inputRef` (string nullable)
  - `planRef` (string nullable)
  - `manifestRef` (string nullable)
  - `parameters` (object)
  - `pendingParametersPatch` (object nullable)
  - `paused` (bool)
  - `awaitingExternal` (bool)
  - `stepCount` (int)
  - `waitCycleCount` (int)
  - `rerunCount` (int)
  - `createIdempotencyKey` (string nullable)
  - `lastUpdateIdempotencyKey` (string nullable)
  - `lastUpdateResponse` (object nullable)
  - `startedAt` (datetime)
  - `updatedAt` (datetime)
  - `closedAt` (datetime nullable)
- **Rules**:
  - This record is an implementation detail, not the public contract.
  - `state`, `closeStatus`, timestamps, and summary metadata must stay mutually consistent.
  - Terminal states must have stable `temporalStatus` projection for API serialization.

## Entity: ExecutionSearchAttributes

- **Description**: Indexed metadata returned as `searchAttributes` and used for list filtering/sorting.
- **Fields**:
  - `mm_owner_id` (string)
  - `mm_state` (string)
  - `mm_updated_at` (datetime string)
  - `mm_entry` (string)
  - extensible additional keys (object)
- **Rules**:
  - Baseline keys above are required in contract-compliant responses.
  - Clients must tolerate additional keys.
  - `mm_updated_at` should track meaningful lifecycle/progress changes.

## Entity: ExecutionMemo

- **Description**: Small display-oriented execution metadata returned as `memo`.
- **Fields**:
  - `title` (string)
  - `summary` (string)
  - `input_ref` (string nullable)
  - `manifest_ref` (string nullable)
  - extensible additional keys (object)
- **Rules**:
  - `title` and `summary` are baseline required keys.
  - Memo remains bounded and display-safe; large payloads stay in artifacts.

## Entity: ExecutionModel

- **Description**: Canonical public execution response body for create, describe, signal, cancel, and list items.
- **Fields**:
  - `namespace` (string)
  - `workflowId` (string)
  - `runId` (string)
  - `workflowType` (string)
  - `state` (string)
  - `temporalStatus` (enum: `running|completed|failed|canceled`)
  - `closeStatus` (string nullable)
  - `searchAttributes` (`ExecutionSearchAttributes`)
  - `memo` (`ExecutionMemo`)
  - `artifactRefs` (array[string])
  - `startedAt` (datetime)
  - `updatedAt` (datetime)
  - `closedAt` (datetime nullable)
- **Rules**:
  - Uses camelCase field names.
  - Must not expose `taskId`.
  - Remains stable even if the backend read path changes later.

## Entity: ExecutionListResponse

- **Description**: Public paginated collection of execution models.
- **Fields**:
  - `items` (array[`ExecutionModel`])
  - `nextPageToken` (string nullable)
  - `count` (integer nullable)
  - `countMode` (enum: `exact|estimated_or_unknown`)
- **Rules**:
  - `nextPageToken` is opaque to clients.
  - `countMode` tells clients whether `count` is authoritative.
  - Current runtime may return `countMode = exact`, but the schema must preserve both options.

## Entity: CreateExecutionRequest

- **Description**: Request contract for `POST /api/executions`.
- **Fields**:
  - `workflowType` (enum)
  - `title` (string nullable)
  - `inputArtifactRef` (string nullable)
  - `planArtifactRef` (string nullable)
  - `manifestArtifactRef` (string nullable)
  - `failurePolicy` (enum nullable: `fail_fast|continue_and_report|best_effort`)
  - `initialParameters` (object)
  - `idempotencyKey` (string nullable)
- **Rules**:
  - `manifestArtifactRef` is required for `MoonMind.ManifestIngest`.
  - `initialParameters` must stay small and JSON-serializable.
  - Create deduplication keys off `(ownerId, workflowType, idempotencyKey)`.

## Entity: UpdateExecutionRequest

- **Description**: Request contract for `POST /api/executions/{workflowId}/update`.
- **Fields**:
  - `updateName` (enum: `UpdateInputs|SetTitle|RequestRerun`, default `UpdateInputs`)
  - `inputArtifactRef` (string nullable)
  - `planArtifactRef` (string nullable)
  - `parametersPatch` (object nullable)
  - `title` (string nullable)
  - `idempotencyKey` (string nullable)
- **Rules**:
  - `SetTitle` requires `title`.
  - Terminal executions return a non-accepted response instead of mutating state.
  - Only the most recent matching update idempotency key is replayable.

## Entity: UpdateExecutionResponse

- **Description**: Synchronous outcome envelope for execution updates.
- **Fields**:
  - `accepted` (bool)
  - `applied` (enum: `immediate|next_safe_point|continue_as_new`)
  - `message` (string)
- **Rules**:
  - Returned with HTTP 200 for both accepted updates and terminal no-op rejection.
  - Must clearly distinguish timing semantics from acceptance semantics.

## Entity: SignalExecutionRequest

- **Description**: Request contract for `POST /api/executions/{workflowId}/signal`.
- **Fields**:
  - `signalName` (enum: `ExternalEvent|Approve|Pause|Resume`)
  - `payload` (object, default `{}`)
  - `payloadArtifactRef` (string nullable)
- **Rules**:
  - `ExternalEvent` requires `payload.source` and `payload.event_type`.
  - `Approve` requires `payload.approval_type`.
  - Terminal executions reject signals with `signal_rejected`.

## Entity: CancelExecutionRequest

- **Description**: Optional-body request contract for `POST /api/executions/{workflowId}/cancel`.
- **Fields**:
  - `reason` (string nullable)
  - `graceful` (bool, default `true`)
- **Rules**:
  - Graceful cancel maps to `state=canceled`, `closeStatus=canceled`.
  - Forced termination maps to `state=failed`, `closeStatus=terminated`.
  - Cancel on a terminal execution returns the unchanged execution model.

## Entity: ExecutionOwnershipScope

- **Description**: Authorization context for create/list/read/control operations.
- **Fields**:
  - `actorId` (string)
  - `isAdmin` (bool)
  - `effectiveOwnerId` (string nullable)
  - `visible` (bool)
- **Rules**:
  - Non-admin callers list only their own executions unless they explicitly repeat their own `ownerId`.
  - Non-admin cross-owner direct access must return a non-disclosing `execution_not_found`.
  - Admin callers may scope lists across owners.

## Entity: DomainErrorEnvelope

- **Description**: Stable router-raised error payload for domain-specific failures.
- **Fields**:
  - `detail.code` (string)
  - `detail.message` (string)
- **Rules**:
  - Known codes include `execution_not_found`, `execution_forbidden`, `invalid_execution_request`, `invalid_update_request`, `invalid_pagination_token`, and `signal_rejected`.
  - Framework validation errors may still use FastAPI/Pydantic-native shapes outside this envelope.

## Entity: ExecutionCompatibilityAdapter

- **Description**: Migration-layer mapping from execution-shaped records into task-oriented or dashboard-oriented payloads.
- **Fields**:
  - `workflowId` (string)
  - `taskId` (string, must equal `workflowId` for Temporal-backed work)
  - `runId` (string)
  - `status/title/summary` (adapter-specific fields)
- **Rules**:
  - Exists outside the direct `/api/executions` contract.
  - Preserves `taskId == workflowId` while task surfaces remain user-facing.

## State Transitions

- **Nominal lifecycle**:
  - `initializing -> planning -> executing -> finalizing -> succeeded`
- **External wait loop**:
  - `executing <-> awaiting_external`
- **Terminal exits**:
  - `initializing|planning|executing|awaiting_external|finalizing -> failed`
  - `initializing|planning|executing|awaiting_external|finalizing -> canceled`
- **Forced termination**:
  - any non-terminal state -> `failed` with `closeStatus=terminated`
- **Rerun / Continue-As-New**:
  - preserve `workflowId`
  - allocate new `runId`
  - reset to a non-terminal state

## Invariants

- `/api/executions` never exposes `taskId`.
- `workflowId` is always the canonical durable handle.
- `runId` is never treated as the durable identifier.
- `ExecutionModel` and `ExecutionListResponse` stay camelCase.
- Baseline search attributes and memo keys are always present in compliant responses.
- Direct unauthorized fetch/control operations do not disclose existence.
- `nextPageToken` remains opaque regardless of internal encoding.
- Compatibility adapters preserve `taskId == workflowId` outside this API.
