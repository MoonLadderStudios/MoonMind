# Data Model: Run History and Rerun Semantics

## Entity: LogicalExecutionIdentity

- **Description**: Durable identity for one Temporal-backed logical execution across Continue-As-New transitions.
- **Fields**:
  - `workflowId` (string, pattern `mm:<uuid-or-ulid>`)
  - `taskId` (string, equals `workflowId` for Temporal-backed rows)
  - `namespace` (string)
  - `workflowType` (enum: `MoonMind.Run` | `MoonMind.ManifestIngest`)
- **Rules**:
  - `workflowId` is the canonical user-facing route and bookmark identifier.
  - `taskId == workflowId` is a compatibility invariant for Temporal-backed payloads.
  - Creating new logical work requires a new `workflowId`; rerun preserves the existing one.

## Entity: TemporalRunInstance

- **Description**: Metadata for the current concrete Temporal run instance of one logical execution.
- **Fields**:
  - `runId` (string)
  - `temporalRunId` (string, explicit alias of `runId` in external payloads)
  - `continueAsNewCause` (enum: `manual_rerun` | `lifecycle_threshold` | `major_reconfiguration` | null)
  - `isLatestRunView` (bool)
- **Rules**:
  - `runId` may change on any Continue-As-New event.
  - `runId` is diagnostic and artifact-resolution metadata, not the primary product route key.
  - `latestRunView` must remain `true` for v1 execution detail payloads.

## Entity: ExecutionProjectionRecord

- **Description**: Latest-run application projection row persisted in `temporal_executions`.
- **Fields**:
  - `workflowId` (PK)
  - `runId` (current Temporal run identifier)
  - `namespace` (string)
  - `workflowType` (enum)
  - `ownerId` (string nullable)
  - `state` (enum: `initializing|planning|executing|awaiting_external|finalizing|succeeded|failed|canceled`)
  - `closeStatus` (enum nullable)
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
  - `startedAt` (datetime, logical execution start)
  - `updatedAt` (datetime)
  - `closedAt` (datetime nullable)
- **Rules**:
  - There is exactly one projection row per logical execution.
  - `startedAt` remains the logical execution start across rerun and rollover.
  - The row is mutable latest-run state, not immutable per-run history.

## Entity: ExecutionMemoEnvelope

- **Description**: Bounded summary metadata attached to the current latest-run view.
- **Fields**:
  - `title` (string)
  - `summary` (string)
  - `input_ref` (string nullable)
  - `manifest_ref` (string nullable)
  - `continue_as_new_cause` (string nullable)
  - `latest_temporal_run_id` (string nullable)
  - `error_category` (string nullable)
- **Rules**:
  - `continue_as_new_cause` records why the current run rotated.
  - `latest_temporal_run_id` should match the current projection `runId` after Continue-As-New.
  - Memo remains human-readable and small; large payloads stay in artifacts.

## Entity: ExecutionDetailView

- **Description**: API payload returned by `/api/executions/{workflowId}` for task/detail rendering.
- **Fields**:
  - `taskId`
  - `workflowId`
  - `runId`
  - `temporalRunId`
  - `workflowType`
  - `state`
  - `temporalStatus`
  - `closeStatus`
  - `searchAttributes`
  - `memo`
  - `artifactRefs`
  - `latestRunView`
  - `continueAsNewCause`
  - `startedAt`
  - `updatedAt`
  - `closedAt`
- **Rules**:
  - The payload always describes the latest/current run for the logical execution.
  - The route key is `workflowId`, not `runId`.
  - Detail rendering may display current `runId` in metadata, but not require run selection in v1.

## Entity: TemporalTaskListRow

- **Description**: Dashboard row model for one Temporal-backed logical execution.
- **Fields**:
  - `id` (string, stable row identity)
  - `taskId` (string)
  - `workflowId` (string)
  - `runId` (string current run metadata)
  - `source` (`temporal`)
  - `title` (string)
  - `rawStatus` (string)
  - `createdAt` (datetime)
  - `startedAt` (datetime logical execution start)
  - `updatedAt` (datetime)
  - `summary` (string)
  - `link` (string `/tasks/{taskId}?source=temporal`)
- **Rules**:
  - `id`, `taskId`, and `workflowId` must all remain stable across Continue-As-New for the same logical execution.
  - `runId`, `updatedAt`, and summary may refresh when the latest run changes.
  - The list must not create sibling rows for the same logical execution solely because `runId` rotated.

## Entity: RerunRequest

- **Description**: Update command used to re-execute the same logical work item.
- **Fields**:
  - `updateName` (`RequestRerun`)
  - `inputArtifactRef` (string nullable)
  - `planArtifactRef` (string nullable)
  - `parametersPatch` (object nullable)
  - `idempotencyKey` (string nullable)
- **Rules**:
  - Accepted rerun preserves `workflowId` and rotates `runId`.
  - Rerun may replace `input_ref`, `plan_ref`, and parameters while remaining the same logical execution.
  - Terminal executions reject rerun in v1 unless a dedicated restart surface is added.

## Entity: ContinueAsNewTransition

- **Description**: Classification and reset bundle for same-execution run rotation.
- **Fields**:
  - `cause` (enum)
  - `previousRunId` (string)
  - `nextRunId` (string)
  - `rerunCount` (int)
  - `clearedPaused` (bool)
  - `clearedAwaitingExternal` (bool)
  - `resetStepCount` (bool)
  - `resetWaitCycleCount` (bool)
  - `nextState` (enum)
- **Rules**:
  - `cause=manual_rerun` implies a user-visible rerun.
  - `cause=lifecycle_threshold|major_reconfiguration` preserves logical identity but is not a manual rerun label.
  - Transition must retain enough metadata to resume the same logical execution safely.

## Entity: ArtifactResolutionContext

- **Description**: Detail-page context needed to fetch artifacts for the latest run while keeping the route logical.
- **Fields**:
  - `namespace` (string)
  - `workflowId` (string)
  - `routeTaskId` (string)
  - `temporalRunId` (string current run)
  - `artifactRefs` (array[string])
- **Rules**:
  - Detail flow must fetch execution detail first, then resolve artifacts using the returned current `temporalRunId`.
  - Route identity remains `routeTaskId == workflowId`.
  - Artifact fetches must tolerate Continue-As-New between list snapshot and detail render.

## State Transitions

- **Manual rerun**:
  - `planning|executing|awaiting_external|finalizing -> planning|executing` with same `workflowId` and new `runId`
- **Run workflow restart target**:
  - without `planRef`: next state `planning`
  - with `planRef`: next state `executing`
- **Manifest ingest restart target**:
  - next state `executing`
- **Automatic rollover**:
  - same logical execution, new `runId`, stable `workflowId`/`taskId`, cause not labeled as manual rerun
- **Terminal update rule**:
  - `succeeded|failed|canceled` reject `RequestRerun` in v1

## Invariants

- `workflowId` is the canonical logical execution identity.
- `taskId == workflowId` for all Temporal-backed compatibility payloads.
- `runId` rotation is expected after rerun or other Continue-As-New causes.
- `startedAt` remains logical execution start until a separate current-run start field exists.
- Latest-run detail/artifact behavior must not require end users to select a historical run.
- The application database remains a latest-run projection, not a v1 run-history ledger.
