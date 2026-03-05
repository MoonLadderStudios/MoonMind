# Data Model: Workflow Type Catalog and Lifecycle

## Entity: WorkflowTypeCatalogEntry

- **Description**: Canonical root-level workflow type definition for v1 catalog behavior.
- **Fields**:
  - `workflowType` (enum: `MoonMind.Run` | `MoonMind.ManifestIngest`)
  - `entry` (enum: `run` | `manifest`)
  - `displayLabel` (string)
  - `stableName` (bool)
- **Rules**:
  - v1 allows only two catalog entries.
  - Type names are stable and cannot be reused for different behavior.

## Entity: WorkflowExecutionIdentity

- **Description**: Stable execution identity across runs and Continue-As-New transitions.
- **Fields**:
  - `workflowId` (string, pattern `mm:<ulid-or-uuid>`)
  - `runId` (string)
  - `namespace` (string)
  - `workflowType` (`WorkflowTypeCatalogEntry.workflowType`)
- **Rules**:
  - `workflowId` is API-visible and must not encode sensitive data.
  - Continue-As-New changes `runId` but preserves `workflowId`.

## Entity: WorkflowExecutionRecord

- **Description**: Materialized execution projection for lifecycle API/list/detail operations.
- **Fields**:
  - `workflowId` (string, PK)
  - `runId` (string)
  - `workflowType` (enum)
  - `ownerId` (string nullable)
  - `state` (enum: `initializing|planning|executing|awaiting_external|finalizing|succeeded|failed|canceled`)
  - `closeStatus` (enum nullable: `completed|failed|canceled|terminated|timed_out|continued_as_new`)
  - `entry` (enum: `run|manifest`)
  - `inputRef` (string nullable)
  - `planRef` (string nullable)
  - `manifestRef` (string nullable)
  - `artifactRefs` (array[string])
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
  - Exactly one dashboard row maps to one workflow execution record.
  - Terminal state and `closeStatus` mapping must be consistent.
  - Non-terminal transitions must keep `closeStatus` null.

## Entity: VisibilityMetadata

- **Description**: Indexed metadata used for list filtering/sorting.
- **Fields**:
  - `mm_owner_id` (keyword)
  - `mm_state` (keyword)
  - `mm_updated_at` (datetime)
  - `mm_entry` (keyword optional for filtering)
- **Rules**:
  - `mm_state` is the required lifecycle filter state.
  - `mm_updated_at` updates on meaningful lifecycle/progress mutations.
  - Visibility filters must be sufficient for dashboard list/filter flows.

## Entity: ExecutionMemo

- **Description**: Bounded display metadata for execution row/detail contexts.
- **Fields**:
  - `title` (string, required)
  - `summary` (string, required)
  - `input_ref` (string optional)
  - `manifest_ref` (string optional)
  - `error_category` (string optional)
- **Rules**:
  - Memo stays small and human-readable.
  - Large payloads are referenced via artifacts rather than embedded.

## Entity: WorkflowUpdateRequest

- **Description**: Request contract for synchronous workflow edits.
- **Fields**:
  - `updateName` (enum: `UpdateInputs|SetTitle|RequestRerun`)
  - `inputArtifactRef` (string nullable)
  - `planArtifactRef` (string nullable)
  - `parametersPatch` (object nullable)
  - `title` (string nullable)
  - `idempotencyKey` (string nullable)
- **Rules**:
  - `SetTitle` requires `title`.
  - Updates are rejected in terminal states.
  - Idempotency key replay returns cached response for duplicate update requests.

## Entity: WorkflowUpdateResponse

- **Description**: Normalized response envelope for update contracts.
- **Fields**:
  - `accepted` (bool)
  - `applied` (enum: `immediate|next_safe_point|continue_as_new`)
  - `message` (string)
- **Rules**:
  - Response always indicates acceptance decision and apply mode.
  - Invalid/unauthorized/invariant-violating requests must reject without mutating protected invariants.

## Entity: WorkflowSignalEvent

- **Description**: Asynchronous signal contract for external/human events.
- **Fields**:
  - `signalName` (enum: `ExternalEvent|Approve|Pause|Resume`)
  - `payload` (object)
  - `payloadArtifactRef` (string nullable)
- **Rules**:
  - `ExternalEvent` requires `payload.source` and `payload.event_type`.
  - `Approve` requires `payload.approval_type`.
  - Signals are rejected in terminal states.

## Entity: LifecyclePolicy

- **Description**: Runtime policy envelope governing transitions, thresholds, and retry behavior.
- **Fields**:
  - `runContinueAsNewStepThreshold` (int)
  - `manifestContinueAsNewPhaseThreshold` (int)
  - `failurePolicy` (enum: `fail_fast|continue_and_report|best_effort`, optional)
  - `activityTimeoutRetryDefaults` (object)
- **Rules**:
  - Continue-As-New thresholds must be configurable and deterministic.
  - Policy may request Continue-As-New for major update/rerun changes.

## Entity: ErrorOutcome

- **Description**: UI-facing failure categorization metadata for terminal failures.
- **Fields**:
  - `errorCategory` (enum: `user_error|integration_error|execution_error|system_error`)
  - `summary` (string)
  - `closeStatus` (enum)
  - `closedAt` (datetime)
- **Rules**:
  - Failed terminal runs must include a supported `errorCategory`.
  - Summary text must remain concise and operator-readable.

## Entity: AuthorizationContext

- **Description**: Control-plane identity and role context for mutation endpoints.
- **Fields**:
  - `actorId` (string)
  - `isAdmin` (bool)
  - `ownerId` (string)
  - `authorized` (bool)
- **Rules**:
  - Update/signal/cancel/rerun require owner or admin authorization.
  - Defense-in-depth checks must reject unauthorized mutations even when called indirectly.

## State Transitions

- **Common lifecycle**:
  - `initializing -> planning -> executing -> finalizing -> succeeded`
  - `initializing|planning|executing|awaiting_external|finalizing -> failed`
  - `initializing|planning|executing|awaiting_external|finalizing -> canceled`
- **Run workflow-specific path**:
  - `executing <-> awaiting_external` via asynchronous event waits.
- **Manifest ingest workflow-specific path**:
  - `initializing -> executing -> finalizing -> succeeded` (with optional repeated execute cycles).
- **Continue-As-New transition**:
  - Preserves `workflowId`, rotates `runId`, resets bounded counters, resumes non-terminal state.

## Invariants

- Dashboard/API row maps to one workflow execution identity.
- Root-level categorization uses workflow type only.
- `mm_state` is always populated and valid.
- Terminal `mm_state` aligns with close status mapping.
- Artifact references are used for large payloads.
- Update and signal names are validated against allowed contract enums.
- Unauthorized mutation requests do not alter lifecycle state.
