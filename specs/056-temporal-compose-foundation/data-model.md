# Data Model: Temporal Compose Foundation

## Entity: TemporalFoundationDeploymentProfile

- **Description**: Canonical runtime profile for self-hosted Temporal foundation in Docker Compose.
- **Fields**:
  - `deploymentMode` (enum: `self_hosted`)
  - `orchestrator` (enum: `docker_compose`)
  - `temporalAddress` (string, default `temporal:7233`)
  - `temporalVersion` (string)
  - `adminToolsVersion` (string)
  - `uiVersion` (string, optional profile)
  - `numHistoryShards` (integer, default `1`)
  - `privateNetworkOnly` (bool)
  - `workerVersioningDefault` (enum: `auto_upgrade`)
- **Rules**:
  - `privateNetworkOnly=true` is required.
  - `numHistoryShards` decision must be recorded before rollout.
  - `workerVersioningDefault` cannot silently drift from `auto_upgrade`.

## Entity: TemporalPersistenceVisibilityConfig

- **Description**: Persistence and visibility storage configuration for Temporal server.
- **Fields**:
  - `persistenceStore` (enum: `postgresql`)
  - `visibilityStore` (enum: `postgresql_sql_advanced_visibility`)
  - `postgresHost` (string)
  - `postgresPort` (int, default `5432`)
  - `primaryDatabase` (string, default `temporal`)
  - `visibilityDatabase` (string, default `temporal_visibility`)
  - `schemaVersion` (string)
  - `rehearsalStatus` (enum: `pending` | `passed` | `failed`)
- **Rules**:
  - Visibility schema rehearsal must pass before upgrade rollout.
  - Both stores must resolve to PostgreSQL for this feature scope.

## Entity: NamespaceRetentionPolicy

- **Description**: Namespace governance for `moonmind` execution retention controls.
- **Fields**:
  - `namespace` (string, default `moonmind`)
  - `retentionDays` (int, default `36500`)
  - `maxStorageGb` (int, default `100`)
  - `policyMode` (enum: `storage_cap_driven`)
  - `reconciledAt` (datetime)
  - `reconciledBy` (string service identity)
- **Rules**:
  - Reconciliation must be idempotent.
  - `maxStorageGb` must be configurable via `TEMPORAL_RETENTION_MAX_STORAGE_GB`.
  - Namespace drift must be corrected by automation, not manual-only action.

## Entity: TemporalExecutionRecord

- **Description**: Runtime execution contract exposed through lifecycle APIs and list views.
- **Fields**:
  - `namespace` (string)
  - `workflowId` (string)
  - `runId` (string)
  - `workflowType` (enum: `MoonMind.Run` | `MoonMind.ManifestIngest`)
  - `state` (enum: `initializing` | `planning` | `executing` | `awaiting_external` | `finalizing` | `succeeded` | `failed` | `canceled`)
  - `taskQueue` (string routing label)
  - `searchAttributes` (map)
  - `memo` (map)
  - `startedAt` (datetime)
  - `updatedAt` (datetime)
  - `closedAt` (datetime nullable)
- **Rules**:
  - Listing, filtering, and pagination are sourced from Temporal Visibility.
  - `taskQueue` is a routing attribute only; no ordering semantics are exposed.
  - Terminal state must align with Temporal close status.

## Entity: ExecutionLifecycleCommand

- **Description**: API command envelope for lifecycle controls.
- **Fields**:
  - `operation` (enum: `start` | `update` | `signal` | `cancel` | `list` | `describe`)
  - `workflowId` (string nullable for `start`/`list`)
  - `requestId` (string idempotency key)
  - `payload` (object)
  - `requestedByUserId` (UUID)
- **Rules**:
  - `update` and `signal` operations must preserve Temporal-native semantics.
  - `cancel` targets execution by `workflowId` and optional `runId`.
  - `list` returns page token contract from Temporal Visibility.

## Entity: VisibilityQueryContract

- **Description**: Query model for execution list/count behavior.
- **Fields**:
  - `filters` (object: owner/state/workflowType/runtime labels)
  - `pageSize` (int default `50`)
  - `nextPageToken` (string nullable)
  - `countMode` (enum: `exact` | `estimated_or_unknown`)
  - `results` (array of `TemporalExecutionRecord`)
- **Rules**:
  - Same filter contract must be used for both list and count behavior.
  - Token is opaque and treated as pass-through from Temporal Visibility APIs.
  - No merged/cross-source pagination.

## Entity: RoutingQueueClass

- **Description**: Queue classification used to route workflow/activity tasks.
- **Fields**:
  - `name` (string; e.g. `mm.workflow`, `mm.activity.llm`)
  - `class` (enum: `workflow` | `activity`)
  - `capability` (enum: `llm` | `sandbox` | `integrations` | `artifacts` | `generic`)
  - `priorityLane` (enum nullable: `high` | `normal` | `low`)
  - `userVisible` (bool, always `false`)
- **Rules**:
  - Queue names are not product-level queue contracts.
  - Any exposed API/UI wording must avoid queue-order guarantees.

## Entity: ManifestExecutionPolicy

- **Description**: Failure behavior contract for manifest ingestion workflow type.
- **Fields**:
  - `policy` (enum: `fail_fast` | `continue_and_report` | `best_effort`)
  - `maxParallelism` (int)
  - `retryOverrides` (object nullable)
  - `aggregationArtifactRef` (string nullable)
- **Rules**:
  - Policy must be explicit in request input; no hidden defaults per request path.
  - Aggregated result artifacts are required for non-fail-fast policies.

## Entity: TemporalScheduleDefinition

- **Description**: Temporal-native recurring trigger definition.
- **Fields**:
  - `scheduleId` (string)
  - `workflowType` (string)
  - `workflowInputRef` (string nullable)
  - `cronOrCalendarSpec` (string/object)
  - `enabled` (bool)
  - `overlapPolicy` (string)
  - `lastTriggeredAt` (datetime nullable)
  - `nextRunAt` (datetime nullable)
- **Rules**:
  - Recurring automation must be represented as Temporal schedule objects.
  - External cron/beat ownership is out of scope for final runtime behavior.

## Entity: UpgradeReadinessRecord

- **Description**: Gate artifact that authorizes or blocks Temporal upgrades.
- **Fields**:
  - `targetTemporalVersion` (string)
  - `targetAdminToolsVersion` (string)
  - `targetVisibilitySchemaVersion` (string)
  - `visibilitySchemaRehearsalPassed` (bool)
  - `shardDecisionAcknowledged` (bool)
  - `workerVersioningPolicyValidated` (bool)
  - `recordedAt` (datetime)
  - `recordedBy` (string)
- **Rules**:
  - Rollout approval requires `visibilitySchemaRehearsalPassed=true`.
  - If shard count is `1`, migration implication acknowledgment is mandatory.

## Entity: ArtifactReferenceEnvelope

- **Description**: Reference payload used by workflows/activities to avoid large history payloads.
- **Fields**:
  - `artifactId` (string)
  - `uri` (string)
  - `contentType` (string)
  - `sizeBytes` (int)
  - `digest` (string nullable)
  - `linkType` (string)
- **Rules**:
  - Large prompts/logs/manifests must use artifact references, not inline workflow payloads.
  - Workflow history payload size checks should reject oversized inline data.

## State Transitions

- **Execution lifecycle**:
  - `initializing -> planning -> executing -> finalizing -> succeeded`
  - `initializing/planning/executing/awaiting_external/finalizing -> failed`
  - `initializing/planning/executing/awaiting_external/finalizing -> canceled`
- **Namespace policy reconciliation**:
  - `pending -> reconciled` (idempotent repeats remain `reconciled`)
- **Upgrade readiness**:
  - `pending -> failed` (any rehearsal/gate failure)
  - `pending -> approved` (all gate checks pass)

## Concurrency and Idempotency

- Lifecycle commands carry idempotency keys to tolerate retried API submissions.
- Namespace reconciliation and schedule upserts are idempotent operations.
- Visibility list tokens are opaque and must not be decoded or mutated by clients.
