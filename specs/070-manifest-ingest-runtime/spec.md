# Feature Specification: Manifest Ingest Runtime

**Feature Branch**: `049-manifest-ingest-runtime`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/Manifests/ManifestIngestDesign.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Manifests/ManifestIngestDesign.md` §5.1 (lines 75-90) | Manifest ingest MUST be a first-class Temporal workflow type named `MoonMind.ManifestIngest`, and Temporal-managed manifest executions MUST surface as `source=temporal`, `entry=manifest` while queue-backed flows remain `source=queue` until migrated. |
| DOC-REQ-002 | `docs/Manifests/ManifestIngestDesign.md` §5.2, §6.2 (lines 94-168) | Each executable manifest node MUST run as a child workflow execution, with ingest-to-run lineage preserved through child workflow inputs and outputs. |
| DOC-REQ-003 | `docs/Manifests/ManifestIngestDesign.md` §5.3, §6.1 (lines 106-150) | Manifest ingest inputs and outputs MUST pass manifests, plans, summaries, and run indexes by artifact reference rather than embedding large payloads in workflow history. |
| DOC-REQ-004 | `docs/Manifests/ManifestIngestDesign.md` §5.4, §6.3 (lines 116-196) | Editable ingest operations MUST use workflow Updates with validation and acknowledgement, while Signals remain limited to optional fire-and-forget nudges. |
| DOC-REQ-005 | `docs/Manifests/ManifestIngestDesign.md` §6.4, §7.1 (lines 200-242) | Manifest ingest MUST expose queryable status and node listings while using the shared canonical lifecycle states `initializing`, `executing`, `finalizing`, `succeeded`, `failed`, and `canceled`. |
| DOC-REQ-006 | `docs/Manifests/ManifestIngestDesign.md` §7.2 (lines 246-258) | Ingest execution MUST follow the documented pipeline of artifact read, parse, validate, compile, persist plan, schedule child runs, await progress, and persist summary artifacts. |
| DOC-REQ-007 | `docs/Manifests/ManifestIngestDesign.md` §8.1-§8.2 (lines 263-301) | Workflow code MUST remain deterministic, runtime selection MUST happen at the activity/task-queue layer, and task queues MUST remain routing boundaries rather than business semantics. |
| DOC-REQ-008 | `docs/Manifests/ManifestIngestDesign.md` §9.1-§9.2 (lines 305-325) | Manifest compilation MUST produce a normalized plan artifact with stable node identifiers that remain consistent across equivalent re-ingests. |
| DOC-REQ-009 | `docs/Manifests/ManifestIngestDesign.md` §10.1-§10.3 (lines 331-373) | Ingest orchestration MUST track pending/ready/running/completed state, enforce bounded concurrency, support `FAIL_FAST` and `BEST_EFFORT`, and require idempotent retriable activities. |
| DOC-REQ-010 | `docs/Manifests/ManifestIngestDesign.md` §11 (lines 377-413) | Ingest runtime MUST respect payload, history, concurrency, update, and signal limits by enforcing reference-based data handling and Continue-As-New checkpointing before limit breaches. |
| DOC-REQ-011 | `docs/Manifests/ManifestIngestDesign.md` §12 (lines 416-429) | Child run workflows started by manifest ingest MUST default to parent close behavior equivalent to request-cancel semantics. |
| DOC-REQ-012 | `docs/Manifests/ManifestIngestDesign.md` §13.1-§13.3 (lines 433-485) | Manifest ingest visibility MUST reuse shared Search Attribute fields, keep Memo small and bounded, and publish a canonical run-index artifact for lineage and totals. |
| DOC-REQ-013 | `docs/Manifests/ManifestIngestDesign.md` §14 (lines 488-502) | Manifest ingest MUST enforce authorization lineage, artifact access controls, and secrecy guardrails that keep secrets and high-cardinality payloads out of workflow history, Search Attributes, and Memo. |
| DOC-REQ-014 | `docs/Manifests/ManifestIngestDesign.md` §15 (lines 506-524) | Manifest ingest MUST implement the minimal activity set for artifact IO, parse, validate, compile, and summary/index persistence, with appropriate retry and idempotency expectations. |
| DOC-REQ-015 | `docs/Manifests/ManifestIngestDesign.md` §16 (lines 528-540) | UI and API surfaces for manifest ingest totals and child-run pagination MUST derive from shared visibility for the ingest itself and the canonical run-index artifact for per-manifest lineage until a shared lineage attribute is standardized. |
| DOC-REQ-016 | MoonMind task instruction Step 2/16 runtime scope guard | Required deliverables MUST include production runtime code changes, not docs/spec-only output, plus validation tests. |

## Clarifications

### Session 2026-03-06

- Q: For v1, what canonical terminal state should `BEST_EFFORT` manifest ingest use when one or more child nodes fail? → A: `failed`; the ingest still completes eligible independent work and writes a partial-failure summary, but the terminal lifecycle must reflect failed work and stay aligned with the shared close-status mapping.
- Q: The shared execution contract already accepts `failurePolicy=continue_and_report`; how should this feature treat it when the source design doc only names `FAIL_FAST` and `BEST_EFFORT`? → A: Preserve `continue_and_report` as an explicit accepted runtime input for compatibility, keep it caller-visible without silent coercion, and require deterministic reporting behavior in planning and validation artifacts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start and execute a manifest ingest (Priority: P1)

As an operator or automation client, I can submit a manifest artifact to a dedicated Temporal ingest workflow that validates, compiles, and launches the required run workflows with durable progress tracking.

**Why this priority**: This is the core runtime capability described by the design doc; without it, manifest artifacts cannot execute through the Temporal-native path.

**Independent Test**: Start a manifest ingest from an artifact reference, then verify the runtime reads and validates the manifest, persists a compiled plan artifact, launches child runs with bounded concurrency, and writes summary/index artifacts at completion.

**Acceptance Scenarios**:

1. **Given** a valid manifest artifact reference and execution policy, **When** a manifest ingest starts, **Then** the system creates a `MoonMind.ManifestIngest` execution, reads the manifest by reference, compiles a plan artifact, and starts child `MoonMind.Run` executions for ready nodes.
2. **Given** an invalid or unauthorized manifest artifact reference, **When** the ingest enters initialization, **Then** the workflow fails before starting child runs and surfaces a validation or authorization failure without embedding the manifest body in workflow history.
3. **Given** a Temporal-managed manifest ingest execution, **When** compatibility or listing views read it, **Then** it appears as `source=temporal` and `entry=manifest` while legacy queue-backed manifest paths remain distinct.

---

### User Story 2 - Edit and inspect an active ingest safely (Priority: P2)

As a user managing a running ingest, I can change future work and inspect current progress through Temporal-native update and query surfaces instead of out-of-band database mutation.

**Why this priority**: Editability and observability are explicit design goals; without them, operators cannot safely adapt or monitor long-running ingest executions.

**Independent Test**: Start a running ingest, invoke updates for manifest replacement/appending, concurrency changes, pause/resume, cancel, and retry, then query status and node lists to confirm the workflow reflects accepted changes and rejects invalid ones.

**Acceptance Scenarios**:

1. **Given** a running ingest with nodes not yet started, **When** a caller submits an `UpdateManifest` or `CancelNodes` request, **Then** the workflow validates the request, preserves already-started work, and updates only eligible future work.
2. **Given** a running ingest, **When** a caller pauses, resumes, or changes concurrency, **Then** the workflow acknowledges the update and subsequent scheduling behavior reflects the new state without direct DB-side mutation.
3. **Given** a large ingest execution, **When** clients query status or paginated node listings, **Then** they receive bounded status data and artifact-backed list results rather than oversized workflow payloads.

---

### User Story 3 - Run large ingests with secure, accurate lineage (Priority: P3)

As a platform operator, I can trust manifest ingest to stay within Temporal limits, protect secrets, and expose accurate totals and lineage for every child run started from a manifest.

**Why this priority**: Scalability, observability, and security constraints are the main architectural reasons this design exists and are required before the runtime can be relied on in production.

**Independent Test**: Exercise manifests large enough to require batching and checkpointing, verify Continue-As-New and concurrency enforcement behavior, and confirm visibility, memo, and run-index outputs preserve lineage without leaking secrets.

**Acceptance Scenarios**:

1. **Given** a manifest whose execution would approach workflow history or concurrency limits, **When** ingest progresses, **Then** the workflow checkpoints orchestration state to artifacts and continues-as-new before crossing documented limits.
2. **Given** a manifest executing in `FAIL_FAST` mode, **When** a node fails, **Then** the ingest stops scheduling new nodes, requests cancellation for running children, and finalizes with a failed terminal state.
3. **Given** a manifest executing in `BEST_EFFORT` mode, **When** some nodes fail, **Then** the ingest continues independent eligible nodes, writes a summary artifact describing partial failures, and closes with terminal state `failed` after finalizing the partial-failure results.
4. **Given** an ingest with multiple child runs, **When** detail views request totals and pagination, **Then** the ingest execution is listed through shared visibility fields and child-run lineage comes from the canonical run-index artifact.

### Edge Cases

- The manifest artifact exists but the caller is not authorized to read it; ingest must fail before parse or compile side effects occur.
- A manifest compiles to more ready nodes than the configured concurrency bound allows; the runtime must queue excess ready nodes without exceeding the hard cap.
- An update attempts to modify a node that already started; the runtime must reject or constrain the change rather than mutate completed history.
- Parent ingest closure occurs while child runs are still active; the default parent-close behavior must request cancellation for those children.
- A manifest completes with mixed success and failure outcomes; the summary artifact and terminal state must stay internally consistent and queryable.
- A caller submits `failurePolicy=continue_and_report`; the runtime must preserve that accepted policy value explicitly and document deterministic handling instead of silently coercing it to another policy label.
- Visibility consumers need lineage before any shared manifest-lineage Search Attribute exists; the run-index artifact must remain the authoritative source for child-run pagination.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST deliver production runtime code changes that introduce `MoonMind.ManifestIngest` as a first-class Temporal workflow type and classify Temporal-managed manifest executions as `source=temporal`, `entry=manifest` without relabeling queue-backed flows that have not migrated. (Maps: DOC-REQ-001, DOC-REQ-016)
- **FR-002**: The system MUST execute each manifest node as a child workflow with ingest-to-run lineage included in child inputs and outputs so every run started by a manifest can be traced back to its ingest execution and node identifier. (Maps: DOC-REQ-002)
- **FR-003**: Manifest ingest runtime inputs and outputs MUST remain reference-based, including manifest, compiled plan, summary, and run-index artifacts, and MUST avoid embedding large manifest or plan payloads in workflow history. (Maps: DOC-REQ-003, DOC-REQ-010)
- **FR-004**: The runtime MUST implement the ingest pipeline as artifact read, parse, validate, compile, persist plan, schedule child runs, await completion/progress, and persist summary/index artifacts. (Maps: DOC-REQ-006, DOC-REQ-014)
- **FR-005**: Editable manifest ingest operations that require validation or acknowledgement MUST be exposed as workflow Updates for replacing or appending future manifest work, changing concurrency, pausing, resuming, canceling pending nodes, and retrying eligible failed nodes. (Maps: DOC-REQ-004)
- **FR-006**: The runtime MAY expose Signals only for optional fire-and-forget nudges and MUST NOT rely on custom database mutation as the source of truth for editable ingest orchestration state. (Maps: DOC-REQ-004)
- **FR-007**: Manifest ingest MUST expose query handlers for current status and bounded ready/running/completed node views and MUST use the shared lifecycle states `initializing`, `executing`, `finalizing`, `succeeded`, `failed`, and `canceled`. (Maps: DOC-REQ-005)
- **FR-008**: Workflow logic for manifest ingest and child execution MUST remain deterministic, while runtime selection for LLM, sandbox, artifact, or integration work MUST occur through activity/task-queue routing rather than workflow-type branching or queue-semantics inventions. (Maps: DOC-REQ-007)
- **FR-009**: Manifest compilation MUST produce a normalized plan artifact containing stable node identifiers so equivalent future re-ingests can preserve idempotent node identity for updates, retries, and lineage. (Maps: DOC-REQ-008)
- **FR-010**: Manifest ingest MUST track pending, ready, running, completed, and failed node state, enforce bounded concurrency by policy, support both `FAIL_FAST` and `BEST_EFFORT` execution policies with deterministic terminal behavior, and preserve the already-supported caller-visible `continue_and_report` policy value without silent coercion; in v1, any `BEST_EFFORT` ingest that finishes with one or more failed nodes MUST close in canonical terminal state `failed` after completing all still-eligible work. (Maps: DOC-REQ-009)
- **FR-011**: The runtime MUST default manifest ingest concurrency to a bounded policy, MUST prevent per-workflow child execution fan-out from exceeding documented safety caps, and MUST checkpoint orchestration state to artifacts before Continue-As-New when history or concurrency pressure approaches limits. (Maps: DOC-REQ-009, DOC-REQ-010)
- **FR-012**: Child runs started by manifest ingest MUST default to request-cancel parent-close semantics so parent cancellation propagates safely to active children. (Maps: DOC-REQ-011)
- **FR-013**: Manifest ingest visibility MUST reuse the shared bounded Search Attribute registry, keep Memo small and display-oriented, and publish a canonical run-index artifact that records per-node child-run lineage and supports totals and pagination. (Maps: DOC-REQ-012, DOC-REQ-015)
- **FR-014**: Manifest ingest MUST enforce caller authorization, artifact read authorization, immutable authorization lineage propagation to child runs, and secrecy guardrails that keep secrets, signed URLs, manifest bodies, and other high-cardinality sensitive payloads out of workflow history, Search Attributes, and Memo. (Maps: DOC-REQ-013)
- **FR-015**: The runtime MUST implement the minimal manifest ingest activity set for artifact read, parse, validate, compile, and summary/index persistence, and each retriable activity MUST be safe for retry through idempotent or equivalent behavior. (Maps: DOC-REQ-014)
- **FR-016**: UI and API detail surfaces for manifest ingest MUST obtain the ingest execution itself from shared visibility fields and MUST use the canonical run-index artifact as the authoritative per-manifest child-run lineage source until a shared lineage Search Attribute is formally standardized. (Maps: DOC-REQ-012, DOC-REQ-015)
- **FR-017**: Delivery MUST include automated validation coverage that exercises manifest ingest startup, child-run orchestration, update/query behavior, bounded scaling behavior, visibility/index outputs, and security guardrails, and those validations MUST run through `./tools/test_unit.sh`. (Maps: DOC-REQ-016)

### Key Entities *(include if feature involves data)*

- **ManifestIngestExecution**: The durable Temporal workflow execution that owns one manifest lifecycle, its execution policy, progress state, and final summary.
- **ManifestExecutionPolicy**: The caller-provided policy that governs concurrency, failure mode, and default routing hints for the ingest.
- **CompiledPlanArtifact**: The normalized manifest plan stored by reference with stable node identifiers, dependency edges, defaults, and large-input references.
- **ManifestNodeExecution**: The per-node orchestration record that tracks readiness, child workflow identity, terminal outcome, and retry eligibility.
- **ManifestIngestStatusSnapshot**: The bounded status view returned by queries and memo metadata for current lifecycle state, phase, counters, and pause/concurrency information.
- **RunIndexArtifact**: The canonical lineage artifact that records node-to-child-run mappings and supports totals, filtering, and pagination for ingest detail views.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid manifest artifact can be submitted through the Temporal-managed ingest path and produces a compiled plan reference, child run lineage, and summary/index artifacts in automated validation coverage.
- **SC-002**: Automated validation confirms every documented edit action for active ingest execution either succeeds with acknowledged workflow state changes or fails fast with a bounded validation error, without requiring direct DB mutation.
- **SC-003**: Automated validation demonstrates that large-manifest execution stays within configured concurrency/history safeguards by checkpointing and continuing-as-new before documented Temporal limits are exceeded.
- **SC-004**: Automated validation confirms that ingest listing, status, totals, and child-run pagination are accurate and consistent across shared visibility fields and the canonical run-index artifact.
- **SC-005**: Automated validation confirms that secrets and unauthorized artifact data are not exposed in workflow history, Search Attributes, Memo, or unauthorized API responses during manifest ingest execution.
- **SC-006**: `./tools/test_unit.sh` passes with manifest ingest runtime coverage added for the required production code paths.

## Assumptions & Constraints

- This feature scopes the Temporal-managed manifest path described in `docs/Manifests/ManifestIngestDesign.md`; queue-backed manifest flows may continue to exist outside this runtime until separately migrated.
- Existing shared Temporal visibility fields and workflow-type catalog contracts remain authoritative and may be extended only through their documented shared contracts.
- Existing shared execution contracts already expose `failurePolicy` values `fail_fast`, `continue_and_report`, and `best_effort`; this feature preserves those accepted runtime inputs even though `docs/Manifests/ManifestIngestDesign.md` only names `FAIL_FAST` and `BEST_EFFORT`.
- For v1 runtime behavior, manifest nodes always execute as child workflows rather than introducing a parallel inline-only node execution mode.
- For `BEST_EFFORT` mode, the ingest continues eligible independent work and writes explicit partial-failure summaries; if any node finishes failed, the ingest finalizes in canonical terminal state `failed` rather than inventing a pseudo-state or masking the failure as success.
- Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Dependencies

- The Artifact System can read and write manifest, plan, summary, checkpoint, and run-index artifacts by reference.
- Temporal runtime infrastructure and workers are available for workflow and activity task-queue execution.
- Existing `MoonMind.Run` workflow execution surfaces can accept ingest lineage metadata and return bounded result summaries.
- Validation infrastructure can execute automated tests through `./tools/test_unit.sh`.
