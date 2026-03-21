# Feature Specification: Manifest Phase 0 Temporal Alignment

**Feature Branch**: `086-manifest-phase0`
**Created**: 2026-03-17  
**Status**: DEPRECATED — merged into `070-manifest-ingest`  
**Input**: User description: "Update 032-manifest-phase0 based on docs/RAG/ManifestTaskSystem.md and fully implement it. Merge in any still relevant functionality from 034 in preparation of deleting the obsolete 034 spec."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

> [!WARNING]
> This spec has been merged into `specs/070-manifest-ingest/`. All requirements from this spec are now covered by 070. The source document `ManifestTaskSystem.md` has been merged into `docs/RAG/ManifestIngestDesign.md`. See that spec and doc for the authoritative requirements.

## Source Document Requirements

| DOC-REQ ID | Source Reference | Requirement Summary |
|------------|------------------|---------------------|
| DOC-REQ-001 | ManifestTaskSystem.md §7.2 | Compile manifest YAML into a `CompiledManifestPlanModel` with stable node IDs, required capabilities, and dependency edges via the `manifest_compile` Activity. |
| DOC-REQ-002 | ManifestTaskSystem.md §7.3 | Node IDs must be deterministically derived from manifest content using SHA-256 hashing for idempotent fan-out. |
| DOC-REQ-003 | ManifestTaskSystem.md §7.4 | Manifest runs must track content-addressable `manifestHash` and `manifestVersion` for change detection and audit. |
| DOC-REQ-004 | ManifestTaskSystem.md §10 | Temporal inputs and workflow history must not contain raw secrets; validation must reject raw secret material before persistence. |
| DOC-REQ-005 | ManifestTaskSystem.md §8 | The workflow must support 6 Temporal Updates: `UpdateManifest`, `SetConcurrency`, `Pause`, `Resume`, `CancelNodes`, `RetryNodes`. |
| DOC-REQ-006 | ManifestTaskSystem.md §7.5 | Manifest runs must produce summary, run-index, and checkpoint artifacts stored in MinIO via artifact Activities. |
| DOC-REQ-007 | ManifestTaskSystem.md §9 | Cancellation must propagate to child workflows via `ParentClosePolicy.REQUEST_CANCEL` and support per-node cancellation via the `CancelNodes` Update. |
| DOC-REQ-008 | ManifestTaskSystem.md §6.2 | Execution policy `options` may override run-control fields (`failurePolicy`, `maxConcurrency`) but must not override structural fields. |
| DOC-REQ-009 | ManifestTaskSystem.md §7.2 stage 4 | Fan-out must spawn child `MoonMind.Run` workflows per manifest node with concurrency control and failure policy enforcement. |
| DOC-REQ-010 | 034 spec FR-003, FR-005 | Manifest queue submission must enforce deterministic normalization including required metadata derivation and capability routing. |
| DOC-REQ-011 | 034 spec FR-004 | Queue and registry responses must remain token-safe by exposing only sanitized references/metadata. |
| DOC-REQ-012 | Runtime scope guard | Delivery must include production runtime code changes and validation tests; docs/spec-only completion is not acceptable. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manifest Compile and Plan Validation (Priority: P1)

An operator submits a manifest for ingestion. The system must compile the manifest YAML into a validated plan with stable node IDs, required capabilities, and dependency information before execution begins.

**Why this priority**: Compilation and plan generation is the entry point for all manifest execution; without a correct compile step, no downstream work can proceed.

**Independent Test**: Submit a manifest artifact reference to the `MoonMind.ManifestIngest` workflow and verify the compile stage produces a `CompiledManifestPlanModel` with stable node IDs, manifest hash, and required capabilities.

**Acceptance Scenarios**:

1. **Given** a valid v0 manifest YAML artifact, **When** the `MoonMind.ManifestIngest` workflow compiles it, **Then** the result contains stable node IDs, manifest hash, required capabilities, and correctly derived plan nodes.
2. **Given** an invalid manifest YAML artifact (missing required fields, unsupported version), **When** compilation is attempted, **Then** the workflow fails with an actionable `ManifestIngestValidationError`.
3. **Given** a manifest with data sources, **When** node IDs are computed, **Then** the same manifest content always produces the same node IDs across runs.

---

### User Story 2 - Manifest Workflow Updates and Interactive Control (Priority: P2)

An operator running a manifest ingest needs to interactively control execution: pause/resume processing, adjust concurrency, cancel specific nodes, retry failed nodes, or update the manifest mid-flight.

**Why this priority**: Interactive control is critical for production operations where operators need to respond to runtime conditions without restarting entire workflows.

**Independent Test**: Start a `MoonMind.ManifestIngest` workflow and exercise each of the 6 Temporal Updates, verifying the workflow state changes accordingly.

**Acceptance Scenarios**:

1. **Given** a running manifest ingest, **When** the `Pause` Update is sent, **Then** no new nodes are scheduled and running nodes complete normally.
2. **Given** a paused manifest ingest, **When** the `Resume` Update is sent, **Then** node scheduling resumes.
3. **Given** a running manifest ingest, **When** `SetConcurrency` is sent with a valid value, **Then** the concurrency limit is adjusted immediately.
4. **Given** a running manifest ingest, **When** `CancelNodes` is sent with pending/ready node IDs, **Then** those nodes transition to `canceled` state.
5. **Given** a manifest ingest with failed nodes, **When** `RetryNodes` is sent, **Then** those nodes reset to `pending` state.
6. **Given** a running manifest ingest, **When** `UpdateManifest` is sent with a new manifest artifact ref, **Then** the update is applied at the next safe point.

---

### User Story 3 - Fan-Out Execution with Failure Policy (Priority: P3)

An operator submits a multi-source manifest. The system must fan out execution to child `MoonMind.Run` workflows per manifest node, respecting concurrency limits, dependency ordering, and the configured failure policy.

**Why this priority**: Fan-out execution is the core runtime behavior; it must handle concurrent execution safely and enforce failure policies to prevent silent partial failures.

**Independent Test**: Submit a manifest with multiple data sources and verify child workflows are spawned with correct concurrency limits and failure policy enforcement.

**Acceptance Scenarios**:

1. **Given** a manifest with 5 data sources and `maxConcurrency=2`, **When** execution proceeds, **Then** at most 2 child `MoonMind.Run` workflows run concurrently.
2. **Given** a manifest with `failurePolicy=fail_fast` and one node fails, **When** the failure is detected, **Then** remaining pending/ready nodes are canceled.
3. **Given** a manifest with `failurePolicy=continue` and one node fails, **When** the failure is detected, **Then** other independent nodes continue execution.
4. **Given** nodes with dependency ordering, **When** execution proceeds, **Then** dependent nodes do not start until their prerequisites succeed.

---

### User Story 4 - Summary and Run-Index Artifact Generation (Priority: P4)

After manifest execution completes, the system must produce summary and run-index artifacts that capture the final state of all nodes, enabling post-run analysis and incremental sync.

**Why this priority**: Artifact generation provides the operational audit trail and enables incremental sync for subsequent runs.

**Independent Test**: Complete a manifest ingest workflow and verify summary and run-index artifacts are written to MinIO with correct content.

**Acceptance Scenarios**:

1. **Given** a completed manifest ingest (all nodes succeeded), **When** finalization runs, **Then** a `ManifestIngestSummaryModel` artifact is written with `state=succeeded` and correct node counts.
2. **Given** a completed manifest ingest with some failed nodes, **When** finalization runs, **Then** the summary artifact includes `failedNodeIds` and `state=failed`.
3. **Given** a completed manifest ingest, **When** finalization runs, **Then** a `ManifestRunIndexModel` artifact is written with per-node state, child workflow IDs, and result artifact references.

### Edge Cases

- UpdateManifest with `APPEND` mode must reject duplicate node IDs from the new manifest.
- UpdateManifest with `REPLACE_FUTURE` mode must preserve running/succeeded/failed nodes and only replace pending/ready nodes.
- `requestedBy` must be validated against the workflow's immutable `mm_owner_id` search attribute; mismatches must be rejected.
- Workflows receiving cancellation while a manifest update is pending must cancel cleanly without applying the update.
- Manifest YAML containing raw secret material must be rejected at compile time, not at execution time.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (DOC-REQ-001, DOC-REQ-003)**: The `manifest_compile` Activity MUST produce a `CompiledManifestPlanModel` with stable node IDs, manifest hash, required capabilities, and plan edges from valid v0 manifest YAML.
- **FR-002 (DOC-REQ-002)**: Node IDs MUST be deterministically derived as `node-{sha256(json.dumps(data_source, sort_keys=True))[:12]}` for idempotent execution.
- **FR-003 (DOC-REQ-004, DOC-REQ-011)**: Manifest compilation and normalization MUST reject raw secret material and surface only safe reference metadata (`profile://`, `vault://`, `${ENV}`).
- **FR-004 (DOC-REQ-005)**: The `MoonMind.ManifestIngest` workflow MUST implement 6 Temporal Updates: `UpdateManifest`, `SetConcurrency`, `Pause`, `Resume`, `CancelNodes`, `RetryNodes`.
- **FR-005 (DOC-REQ-006)**: The workflow MUST produce summary (`ManifestIngestSummaryModel`) and run-index (`ManifestRunIndexModel`) artifacts on finalization.
- **FR-006 (DOC-REQ-007)**: Cancellation MUST propagate to child workflows via `ParentClosePolicy.REQUEST_CANCEL` and support per-node cancellation via `CancelNodes`.
- **FR-007 (DOC-REQ-008)**: Execution policy `options` MUST be limited to `failurePolicy` and `maxConcurrency`; structural manifest fields MUST NOT be overridable.
- **FR-008 (DOC-REQ-009)**: Fan-out execution MUST spawn child `MoonMind.Run` workflows per manifest node with configurable concurrency limits and dependency-aware scheduling.
- **FR-009 (DOC-REQ-010)**: Manifest queue submission MUST enforce deterministic normalization via the manifest contract, including capability derivation and metadata hashing.
- **FR-010 (DOC-REQ-011)**: Queue and registry API responses MUST sanitize manifest payloads, hiding raw YAML content while retaining safe metadata.
- **FR-011 (DOC-REQ-012)**: All functional requirements above MUST be covered by unit tests executed via `./tools/test_unit.sh`.

### Key Entities

- **CompiledManifestPlanModel**: The compiled plan output from manifest validation — contains nodes, edges, required capabilities, manifest digest.
- **ManifestNodeModel**: Runtime representation of a manifest data source node with lifecycle state (`pending`/`ready`/`running`/`succeeded`/`failed`/`canceled`).
- **ManifestExecutionPolicyModel**: Per-workflow concurrency and failure policy configuration.
- **ManifestStatusSnapshotModel**: Bounded query response for API status endpoints.
- **ManifestIngestSummaryModel**: Final summary artifact with node counts and failed node IDs.
- **ManifestRunIndexModel**: Per-node state index artifact with child workflow references.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Manifest compilation produces deterministic node IDs — running the same manifest twice yields identical node graphs in automated tests.
- **SC-002**: All 6 Temporal Updates produce correct state transitions verified by unit tests.
- **SC-003**: Fan-out execution respects configured concurrency limits and failure policies in automated tests.
- **SC-004**: Summary and run-index artifacts contain correct node state data verified by unit tests against known manifest inputs.
- **SC-005**: Secret leak detection rejects manifests with raw tokens/keys and accepts manifests using only safe references in automated tests.
- **SC-006**: `./tools/test_unit.sh` passes with all manifest-related test suites.
- **SC-007**: Queue and registry API responses do not expose raw manifest YAML content in automated tests.

## Assumptions & Constraints

- The `MoonMind.ManifestIngest` workflow and its supporting modules already exist in the codebase; this spec covers hardening, test coverage, and alignment with the updated `ManifestTaskSystem.md` design document.
- Child `MoonMind.Run` workflow execution is out of scope for this spec — only the fan-out spawning and result collection from the parent workflow perspective are in scope.
- Temporal infrastructure (server, workers, namespaces) is assumed operational per `docs/Temporal/TemporalArchitecture.md`.
- This spec subsumes the scope of both `specs/032-manifest-phase0` and `specs/034-manifest-phase0`.
