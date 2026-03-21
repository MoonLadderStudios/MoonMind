# Data Model: Manifest Ingest Runtime

## Entity: ManifestIngestWorkflowInput

- **Description**: Canonical workflow input used to start `MoonMind.ManifestIngest`.
- **Fields**:
  - `workflowType` (const: `MoonMind.ManifestIngest`)
  - `manifestRef` (artifact ref)
  - `requestedBy` (object: `type`, `id`)
  - `executionPolicy` (`ManifestExecutionPolicy`)
  - `tags` (map[string]string)
  - `registryName` (string nullable, for registry-backed submissions)
- **Rules**:
  - Manifest bytes are never embedded directly; `manifestRef` is required.
  - `requestedBy` is immutable for the lifetime of the ingest and is propagated to child runs.

## Entity: ManifestExecutionPolicy

- **Description**: Caller-visible policy envelope that governs scheduling behavior.
- **Fields**:
  - `failurePolicy` (enum: `fail_fast|continue_and_report|best_effort`)
  - `maxConcurrency` (int)
  - `concurrencyDefaulted` (bool)
  - `defaultTaskQueues` (object nullable)
  - `scheduleBatchSize` (int)
- **Rules**:
  - `maxConcurrency` is bounded by config-driven defaults and hard caps.
  - `best_effort` with any failed node still yields terminal ingest state `failed`.

## Entity: CompiledManifestPlanArtifact

- **Description**: Normalized artifact-backed execution plan produced after parse/validate/compile.
- **Fields**:
  - `planRef` (artifact ref)
  - `manifestDigest` (sha256 string)
  - `planVersion` (string)
  - `nodes` (array of `CompiledManifestPlanNode`)
  - `edges` (array of `fromNodeId`, `toNodeId`)
  - `totals` (object: pending/ready/running/completed/failed counts)
  - `compileMetadata` (object: compiler version, normalized-at timestamp)
- **Rules**:
  - Equivalent manifests must generate the same logical node IDs.
  - Large node inputs are stored by artifact reference, not embedded inline.

## Entity: CompiledManifestPlanNode

- **Description**: One executable or non-executable node in the normalized plan.
- **Fields**:
  - `nodeId` (string, stable deterministic identifier)
  - `kind` (string)
  - `title` (string)
  - `dependsOn` (array[string])
  - `inputRef` (artifact ref nullable)
  - `runtimeHints` (object)
  - `childWorkflowType` (const for v1: `MoonMind.Run`)
  - `retryPolicy` (object nullable)
- **Rules**:
  - `nodeId` cannot depend on traversal order or runtime-generated values.
  - Nodes already started in a prior run segment are immutable to future manifest replacement.

## Entity: ManifestNodeRuntimeState

- **Description**: Workflow-owned orchestration state for one node during ingest execution.
- **Fields**:
  - `nodeId` (string)
  - `state` (enum: `pending|ready|starting|running|succeeded|failed|canceled|skipped`)
  - `attempt` (int)
  - `childWorkflowId` (string nullable)
  - `childRunId` (string nullable)
  - `resultRef` (artifact ref nullable)
  - `failureSummary` (object nullable)
  - `startedAt` (datetime nullable)
  - `updatedAt` (datetime)
- **Rules**:
  - `running` implies a child workflow identity exists.
  - `RetryNodes` creates a new child run identity while preserving the same `nodeId`.

## Entity: ManifestIngestCheckpointArtifact

- **Description**: Artifact-backed snapshot of workflow scheduling state used for Continue-As-New recovery.
- **Fields**:
  - `checkpointRef` (artifact ref)
  - `workflowId` (string)
  - `runId` (string)
  - `planRef` (artifact ref)
  - `pendingNodeIds` (array[string])
  - `readyNodeIds` (array[string])
  - `runningNodes` (array of `nodeId`, `childWorkflowId`, `childRunId`)
  - `completedNodes` (array of `nodeId`, `state`, `resultRef`)
  - `executionPolicy` (`ManifestExecutionPolicy`)
  - `runIndexRef` (artifact ref nullable)
- **Rules**:
  - Checkpoints are written before Continue-As-New when thresholds are crossed.
  - Checkpoint payloads must be resumable without rereading large manifest content from history.

## Entity: ManifestIngestStatusSnapshot

- **Description**: Bounded status shape returned by workflow query surfaces and API detail serialization.
- **Fields**:
  - `workflowId` (string)
  - `runId` (string)
  - `state` (enum: `initializing|executing|finalizing|succeeded|failed|canceled`)
  - `phase` (string bounded)
  - `paused` (bool)
  - `failurePolicy` (string)
  - `maxConcurrency` (int)
  - `counts` (object: pending/ready/running/succeeded/failed/canceled)
  - `planRef` (artifact ref nullable)
  - `summaryRef` (artifact ref nullable)
  - `runIndexRef` (artifact ref nullable)
  - `checkpointRef` (artifact ref nullable)
  - `updatedAt` (datetime)
- **Rules**:
  - Query payloads remain bounded and cannot inline the full node graph.
  - `runIndexRef` is the authoritative per-manifest lineage source for UI/API pagination.

## Entity: ManifestIngestSummaryArtifact

- **Description**: Final artifact summarizing ingest outcome, counts, and high-level failure details.
- **Fields**:
  - `summaryRef` (artifact ref)
  - `workflowId` (string)
  - `manifestRef` (artifact ref)
  - `planRef` (artifact ref)
  - `runIndexRef` (artifact ref)
  - `terminalState` (enum: `succeeded|failed|canceled`)
  - `counts` (object)
  - `partialFailure` (bool)
  - `failureHighlights` (array[string])
  - `startedAt` (datetime)
  - `completedAt` (datetime)
- **Rules**:
  - Partial-failure results remain terminal `failed`.
  - Summary stays operator-readable and does not contain raw secrets or manifest body content.

## Entity: RunIndexArtifact

- **Description**: Canonical artifact-backed lineage index for child runs started by one manifest ingest.
- **Fields**:
  - `runIndexRef` (artifact ref)
  - `workflowId` (string)
  - `manifestRef` (artifact ref)
  - `entries` (array of `RunIndexEntry`)
  - `pageInfo` (object: totalCount, cursor fields)
  - `generatedAt` (datetime)
- **Rules**:
  - This artifact is the only authoritative source for per-manifest child-run totals and pagination in v1.
  - The ingest execution itself is still listed via shared Temporal visibility, not via this artifact.

## Entity: RunIndexEntry

- **Description**: One lineage row describing a child run started from a manifest node.
- **Fields**:
  - `nodeId` (string)
  - `childWorkflowId` (string)
  - `childRunId` (string)
  - `workflowType` (string)
  - `state` (string)
  - `resultRef` (artifact ref nullable)
  - `startedAt` (datetime nullable)
  - `completedAt` (datetime nullable)
  - `requestedBy` (object: type, id)
- **Rules**:
  - Entry payloads must remain bounded and should not duplicate large child outputs.
  - Pagination/totals for manifest lineage derive from this artifact, not ad hoc Search Attributes.

## Entity: ManifestIngestUpdateRequest

- **Description**: Union of acknowledged write operations accepted by a running manifest ingest.
- **Variants**:
  - `UpdateManifest` (`newManifestRef`, `mode=REPLACE_FUTURE|APPEND`)
  - `SetConcurrency` (`maxConcurrency`)
  - `Pause`
  - `Resume`
  - `CancelNodes` (`nodeIds`)
  - `RetryNodes` (`nodeIds`)
- **Rules**:
  - Updates that target nodes already started must reject or narrow themselves deterministically.
  - Update validators reject malformed or unauthorized requests before mutating workflow state.

## Entity: AuthorizationLineage

- **Description**: Immutable actor and access context propagated from ingest start to child execution.
- **Fields**:
  - `principalType` (string)
  - `principalId` (string)
  - `tenantId` (string nullable)
  - `orgId` (string nullable)
  - `manifestAccessValidated` (bool)
  - `source` (enum: `registry|artifact`)
- **Rules**:
  - Authorization lineage is copied into child workflow inputs and relevant artifact metadata.
  - Secrets, bearer tokens, signed URLs, and unbounded auth context are never persisted in Memo or Search Attributes.

## Workflow State Transitions

- `initializing -> executing -> finalizing -> succeeded`
- `initializing|executing|finalizing -> failed`
- `initializing|executing|finalizing -> canceled`
- `executing -> executing` is allowed across update handling, scheduling rounds, and Continue-As-New rollover.

## Node State Transitions

- `pending -> ready -> starting -> running -> succeeded`
- `pending|ready -> canceled`
- `running -> failed|canceled|succeeded`
- `failed -> ready` only through validated `RetryNodes`
- `pending|ready -> skipped` when `FAIL_FAST` or replacement logic makes future work ineligible

## Invariants

- Workflow history, Search Attributes, and Memo only carry bounded metadata and artifact refs.
- `mm_entry` remains `manifest` for Temporal-managed manifest ingests.
- Queue-backed manifest flows remain `source=queue` until separately migrated.
- `runIndexRef` is authoritative for child-run lineage pages and totals.
- Child workflows inherit immutable ingest lineage and request-cancel parent-close semantics.
- Continue-As-New preserves `workflowId`, plan identity, and resumable scheduling state through checkpoint artifacts.
- Any `BEST_EFFORT` ingest with one or more failed nodes finalizes in terminal state `failed`.
