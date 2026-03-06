# Data Model: Jules Temporal External Events

## Entity: JulesExecutionHandle

- **Description**: Compact workflow-visible representation of one Jules provider operation.
- **Fields**:
  - `integrationName` (constant string: `jules`)
  - `correlationId` (string)
  - `externalOperationId` (string; Jules `taskId`)
  - `providerStatus` (string)
  - `normalizedStatus` (enum: `queued|running|succeeded|failed|canceled|unknown`)
  - `externalUrl` (string nullable)
  - `callbackSupported` (boolean)
- **Rules**:
  - `providerStatus` always preserves the raw Jules status token after trimming/defaulting.
  - `externalOperationId` is the provider handle and must never replace MoonMind workflow identity.
  - `callbackSupported` remains `false` until a verified Jules callback path exists.

## Entity: JulesCorrelationRecord

- **Description**: MoonMind-owned durable linkage between workflow execution identity and the Jules provider operation.
- **Fields**:
  - `workflowId` (string)
  - `runId` (string or Temporal execution linkage payload)
  - `correlationId` (string; stable across retry and `Continue-As-New`)
  - `integrationName` (constant string: `jules`)
  - `externalOperationId` (string nullable until provider start succeeds)
  - `idempotencyKey` (string)
  - `providerMetadataHints` (small JSON object)
- **Rules**:
  - `correlationId` is MoonMind-generated and is the durable source of truth.
  - Provider metadata hints are optional, bounded, and non-secret.
  - `runId` is not used as the provider idempotency source.

## Entity: JulesStatusSnapshot

- **Description**: One normalized observation of provider state used by polling, reconciliation, and compatibility views.
- **Fields**:
  - `providerStatus` (string)
  - `providerStatusToken` (lowercased string)
  - `normalizedStatus` (enum: `queued|running|succeeded|failed|canceled|unknown`)
  - `terminal` (boolean)
  - `succeeded` (boolean)
  - `failed` (boolean)
  - `canceled` (boolean)
  - `observedAt` (timestamp)
  - `trackingRef` (artifact ref nullable)
- **Rules**:
  - Normalization is produced only by the shared Jules status normalizer.
  - Unknown provider statuses fall back to `unknown`.
  - Terminal states are limited to `succeeded`, `failed`, and `canceled`.

## Entity: JulesResultArtifactSet

- **Description**: Artifact-backed materialization for Jules start, status, result, and failure-summary records.
- **Fields**:
  - `startSnapshotRef` (artifact ref nullable)
  - `statusSnapshotRef` (artifact ref nullable)
  - `terminalSnapshotRef` (artifact ref nullable)
  - `failureSummaryRef` (artifact ref nullable)
  - `resolutionSummaryRef` (artifact ref nullable)
  - `rawCallbackPayloadRef` (artifact ref nullable, future)
  - `executionRef` (Temporal execution link payload)
- **Rules**:
  - Temporal-backed flows must at minimum persist the terminal snapshot.
  - Failed/canceled/unsupported-cancel outcomes require a failure-summary artifact.
  - Large provider payloads are artifact-backed rather than returned inline from activities.

## Entity: JulesExternalEvent

- **Description**: Future bounded callback payload delivered through the generic `ExternalEvent` signal contract.
- **Fields**:
  - `source` (constant string: `jules`)
  - `eventType` (string)
  - `externalOperationId` (string; Jules `taskId`)
  - `providerEventId` (string nullable)
  - `providerStatus` (string nullable)
  - `normalizedStatus` (enum nullable)
  - `observedAt` (timestamp)
  - `payloadArtifactRef` (artifact ref nullable)
- **Rules**:
  - Raw callback bodies are retained only through restricted artifacts.
  - Dedupe keys use bounded provider event identity when available.
  - No workflow logic trusts unauthenticated callback payloads.

## Entity: JulesCancellationOutcome

- **Description**: Workflow-visible record that distinguishes MoonMind-side cancellation from provider-side support.
- **Fields**:
  - `workflowCanceled` (boolean)
  - `providerCancellationAttempted` (boolean)
  - `providerCancellationSupported` (boolean)
  - `providerCancellationSucceeded` (boolean nullable)
  - `summary` (string)
  - `diagnosticsRef` (artifact ref nullable)
- **Rules**:
  - Current Jules behavior sets `providerCancellationSupported=false`.
  - Workflow cancellation may still succeed even when provider cancellation is unsupported.
  - Summaries must explicitly state when provider cancellation was not performed.

## Entity: JulesCompatibilityView

- **Description**: API/dashboard projection for operators during the migration from non-Temporal to Temporal-backed Jules monitoring.
- **Fields**:
  - `taskId` (string; MoonMind compatibility identifier, not Jules `taskId`)
  - `workflowId` (string)
  - `providerTaskId` (string nullable)
  - `integrationName` (string)
  - `normalizedStatus` (enum)
  - `providerStatus` (string)
  - `externalUrl` (string nullable)
  - `artifactRefs` (array[artifact ref])
- **Rules**:
  - `taskId`/`workflowId` remain the durable MoonMind identifiers.
  - `providerTaskId` is shown separately for operator correlation.
  - UI compatibility labels may remain task-oriented during migration.

## State Transitions

- **Provider lifecycle**:
  - `queued -> running -> succeeded`
  - `queued|running -> failed`
  - `queued|running -> canceled`
  - `queued|running -> unknown`
- **Monitoring lifecycle**:
  - `start_submitted -> awaiting_external -> polling`
  - `polling -> terminal_snapshot_materialized`
  - `polling -> callback_observed` (future)
- **Cancellation truthfulness**:
  - `workflow_cancel_requested -> workflow_canceled`
  - `workflow_cancel_requested -> provider_cancel_unsupported` (current Jules posture)

## Invariants

- Jules provider identity is always `jules`.
- Jules provider handle is always the provider `taskId`.
- Raw provider status is preserved even when normalized status is `unknown`.
- Temporal activity payloads stay compact; large payloads move to artifacts.
- Secret values do not enter workflow state, artifacts, or exception text.
- The default activity queue remains `mm.activity.integrations`.
