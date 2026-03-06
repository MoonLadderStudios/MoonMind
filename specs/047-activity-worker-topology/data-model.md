# Data Model: Activity Catalog and Worker Topology

## Entity: TemporalActivityTypeContract

- **Description**: Stable contract record for one public activity type in the Temporal catalog.
- **Fields**:
  - `activityType` (string; dotted namespace such as `artifact.create` or `integration.jules.start`)
  - `family` (enum: `artifact|plan|skill|sandbox|integration|system`)
  - `capabilityClass` (string; `artifacts|llm|sandbox|integration:<provider>|by_capability`)
  - `taskQueue` (string)
  - `fleet` (enum: `workflow|artifacts|llm|sandbox|integrations`)
  - `timeouts` (`ActivityPolicyProfile.timeouts`)
  - `retries` (`ActivityPolicyProfile.retries`)
  - `heartbeatRequired` (boolean)
- **Rules**:
  - Activity type names are stable and must not be repurposed.
  - Every activity type maps to exactly one default queue and fleet in v1.
  - Unsupported activity types fail closed.

## Entity: ActivityPolicyProfile

- **Description**: Default timeout, retry, and heartbeat policy block for one activity family or activity type.
- **Fields**:
  - `startToCloseSeconds` (integer > 0)
  - `scheduleToCloseSeconds` (integer >= `startToCloseSeconds`)
  - `heartbeatTimeoutSeconds` (integer nullable)
  - `maxAttempts` (integer > 0)
  - `maxIntervalSeconds` (integer > 0)
  - `nonRetryableErrorCodes` (array[string])
  - `destructive` (boolean)
  - `costSensitive` (boolean)
- **Rules**:
  - Sandbox long-running operations require heartbeat policies.
  - Destructive or expensive operations use bounded retries.
  - Policy ownership stays in runtime configuration/catalog code, not ad hoc handlers.

## Entity: ActivityInvocationEnvelope

- **Description**: Shared business envelope for one activity execution request or result.
- **Fields**:
  - `correlationId` (string)
  - `idempotencyKey` (string required for side-effecting operations)
  - `inputRefs` (array[`ArtifactLifecycleRecord.artifactRef`])
  - `parameters` (small JSON object)
  - `outputRefs` (array[`ArtifactLifecycleRecord.artifactRef`])
  - `summary` (small JSON object)
  - `metrics` (small JSON object nullable)
  - `diagnosticsRef` (`ArtifactLifecycleRecord.artifactRef`, nullable)
- **Rules**:
  - Large request and result content must be referenced through artifacts.
  - Side-effecting activities require `idempotencyKey`.
  - Result summaries must stay compact and machine-readable.

## Entity: ActivityExecutionContext

- **Description**: Temporal runtime metadata attached to one activity attempt.
- **Fields**:
  - `workflowId` (string)
  - `runId` (string)
  - `activityId` (string)
  - `attempt` (integer)
  - `taskQueue` (string)
- **Rules**:
  - Context is derived from Temporal runtime APIs, not duplicated into business payloads by default.
  - Logging, tracing, and metrics must include these identifiers.

## Entity: CapabilityBinding

- **Description**: Registry-driven mapping from a capability or explicit activity type to queue, fleet, and default policy profile.
- **Fields**:
  - `bindingKey` (string; activity type or capability token)
  - `selectorMode` (enum: `by_capability|explicit`)
  - `capabilityClass` (string)
  - `taskQueue` (string)
  - `fleet` (string)
  - `policyProfile` (`ActivityPolicyProfile`)
- **Rules**:
  - Bindings are resolved per invocation.
  - Workflow type must never determine the binding directly.
  - Invalid or incomplete bindings are rejected before execution.

## Entity: SkillExecutionBinding

- **Description**: Declares how one skill is executed within the hybrid binding model.
- **Fields**:
  - `skillName` (string)
  - `version` (string)
  - `activityType` (string; default `mm.skill.execute`)
  - `requiredCapabilities` (array[string])
  - `explicitBindingReason` (enum nullable: `stronger_isolation|specialized_credentials|clearer_routing`)
  - `policies` (`ActivityPolicyProfile`)
- **Rules**:
  - Explicit activity bindings require `explicitBindingReason`.
  - `mm.skill.execute` routes by declared capability.
  - Missing or unsupported capability declarations invalidate the binding.

## Entity: WorkerFleetProfile

- **Description**: Operational profile for one Temporal worker fleet.
- **Fields**:
  - `fleet` (enum: `workflow|artifacts|llm|sandbox|integrations`)
  - `serviceName` (string)
  - `taskQueues` (array[string])
  - `capabilities` (array[string])
  - `privileges` (array[string])
  - `requiredSecrets` (array[string])
  - `concurrencyLimit` (integer nullable)
  - `resourceClass` (enum: `light|io_bound|cpu_mem_heavy|rate_limited`)
  - `egressPolicy` (string)
- **Rules**:
  - Workflow fleet executes workflow code only.
  - Sandbox fleet cannot receive provider tokens.
  - Integrations fleet cannot run arbitrary shell commands.

## Entity: ArtifactLifecycleRecord

- **Description**: Artifact metadata and lifecycle linkage used across activity families.
- **Fields**:
  - `artifactRef` (object; existing `ArtifactRef`)
  - `executionRef` (object; existing `ExecutionRef`)
  - `linkType` (enum: `input.instructions|input.manifest|input.plan|output.primary|output.patch|output.logs|output.summary|debug.trace`)
  - `retentionClass` (enum: `ephemeral|standard|long|pinned`)
  - `redactionLevel` (enum: `none|preview_only|restricted`)
  - `previewArtifactRef` (artifact ref nullable)
  - `pinState` (enum: `unpinned|pinned`)
- **Rules**:
  - Retention class derives from link type unless pinned.
  - Restricted artifacts may expose only preview content to non-privileged readers.
  - Artifact writes remain retry-safe via content verification and idempotent completion.

## Entity: PlanArtifact

- **Description**: Generated or validated plan persisted as an artifact for runtime execution.
- **Fields**:
  - `planRef` (`ArtifactLifecycleRecord.artifactRef`)
  - `registrySnapshotRef` (`ArtifactLifecycleRecord.artifactRef`)
  - `validatedRef` (`ArtifactLifecycleRecord.artifactRef`, nullable)
  - `policy` (object)
  - `nodeCount` (integer)
- **Rules**:
  - `plan.validate` is the authoritative deep-validation gate.
  - Workflows must execute validated or validation-approved plan artifacts, not inline plan blobs.

## Entity: SandboxWorkspaceRef

- **Description**: Durable reference to one sandbox workspace created for checkout, patching, command execution, and tests.
- **Fields**:
  - `workspaceRef` (string)
  - `repoRef` (string)
  - `idempotencyKey` (string)
  - `checkoutRevision` (string nullable)
  - `appliedPatchRefs` (array[artifact ref])
  - `state` (enum: `created|checked_out|patched|tested|failed|cleaned`)
- **Rules**:
  - Workspace identity is stable for the same repo/idempotency input.
  - Retry must not duplicate checkout or patch side effects unexpectedly.
  - Long-running workspace operations emit heartbeats and diagnostics refs.

## Entity: IntegrationTrackingRecord

- **Description**: Provider-side activity tracking state for one external execution.
- **Fields**:
  - `provider` (string)
  - `externalId` (string)
  - `idempotencyKey` (string)
  - `trackingRef` (artifact ref nullable)
  - `status` (string)
  - `callbackMode` (enum: `callback_first|polling_fallback`)
- **Rules**:
  - Repeat starts with the same idempotency key reuse the same external identity.
  - Provider credentials are available only to the integrations fleet.
  - Polling fallback must remain bounded and workflow-coordinated.

## Entity: ObservabilitySummary

- **Description**: Structured outcome envelope for operators and telemetry systems.
- **Fields**:
  - `workflowId` (string)
  - `runId` (string)
  - `activityType` (string)
  - `activityId` (string)
  - `attempt` (integer)
  - `correlationId` (string)
  - `idempotencyKeyHash` (string)
  - `diagnosticsRef` (artifact ref nullable)
  - `metricsDimensions` (object)
  - `outcome` (enum: `succeeded|failed|cancelled|partial`)
- **Rules**:
  - Large logs belong in artifacts, not inline log bodies.
  - Secret values must be redacted before log or artifact emission.
  - Operators should be able to answer "what happened?" from summary data plus referenced diagnostics.

## State Transitions

- **Artifact lifecycle**:
  - `created -> complete`
  - `complete -> pinned`
  - `complete -> expired -> deleted`
- **Sandbox workspace lifecycle**:
  - `created -> checked_out -> patched -> tested`
  - `created|checked_out|patched|tested -> failed`
- **Integration tracking lifecycle**:
  - `pending -> running -> completed`
  - `pending|running -> failed`
  - `pending|running -> cancelled`

## Invariants

- Every canonical activity type has one stable name and one default v1 route.
- `mm.skill.execute` remains the default skill path unless an explicit binding reason is declared.
- Large inputs, outputs, and logs are represented as artifact references.
- Activities do not update workflow visibility fields directly.
- Sandbox heartbeats are required for long-running operations.
- v1 LLM provider choice does not create new task queues.
- Worker fleet privileges remain least-privilege and capability-scoped.
