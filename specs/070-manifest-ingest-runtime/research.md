# Research: Manifest Ingest Runtime

## Decision 1: Build the feature on the existing Temporal projection/artifact stack, but add real Temporal SDK workflow execution

- **Decision**: Treat the current `TemporalExecutionService`, manifest submission service, artifact service, and worker-fleet topology as the control-plane baseline, then add actual Temporal workflow/client execution support for `MoonMind.ManifestIngest` and child `MoonMind.Run` orchestration.
- **Rationale**: The repo already persists manifest-backed Temporal execution metadata and artifact references, but the current Temporal worker path is still bootstrap-only and no workflow definitions or SDK client layer exist yet. Manifest ingest requires real workflow execution, updates, queries, child workflows, and Continue-As-New behavior.
- **Alternatives considered**:
  - Keep using the projection-only service without Temporal SDK runtime: rejected because it cannot satisfy child workflow orchestration, update/query semantics, or deterministic workflow execution.
  - Reintroduce queue ownership for manifest runtime: rejected because the source design is explicitly Temporal-native and queue-backed flows must remain distinct until separately migrated.

## Decision 2: Preserve the registry-backed submit path, but make artifact-first Temporal execution the runtime authority

- **Decision**: Keep `POST /api/manifests/{name}/runs` as a convenience wrapper that stages registry YAML into an artifact and starts `MoonMind.ManifestIngest`, while the canonical runtime contract remains artifact-first and Temporal-backed.
- **Rationale**: The current repo already stages registry manifests into Temporal artifacts inside `ManifestsService.submit_manifest_run(...)`. Reusing that path minimizes operator churn while still honoring the design requirement that workflow payloads carry references, not manifest bytes.
- **Alternatives considered**:
  - Remove the registry-backed endpoint and require all callers to pre-stage artifacts: rejected because it would regress the current UX without improving runtime semantics.
  - Keep registry content inline in execution inputs: rejected because it violates the artifact-reference contract and increases workflow history risk.

## Decision 3: Compile manifests into a normalized plan artifact with stable node identifiers derived from canonical node content

- **Decision**: Manifest compilation should emit a JSON plan artifact whose node IDs are derived from a canonical node envelope (logical node kind, normalized parameters, dependency labels, and stable manifest path), not from traversal order or transient run metadata.
- **Rationale**: Stable node IDs are required for safe `UpdateManifest`, retry targeting, lineage, and equivalent re-ingests. Canonical-content identity is robust to formatting changes and preserves deterministic comparison across replays.
- **Alternatives considered**:
  - Use sequential node numbers assigned during compile: rejected because equivalent re-ingests could reorder nodes and break update/retry identity.
  - Hash the raw YAML slice for each node: rejected because semantically equivalent formatting changes would generate unstable IDs.

## Decision 4: Implement the minimal manifest ingest activity set as explicit artifact-bounded steps

- **Decision**: Add dedicated manifest-ingest activities for artifact read, parse, validate, compile, plan persistence, summary persistence, and run-index/checkpoint persistence, all returning compact result metadata and artifact references only.
- **Rationale**: The source document requires deterministic workflow code with side effects isolated to activities. The repo already follows this pattern for artifacts and plan generation, so manifest ingest should extend the same activity contract family rather than inventing a second runtime pattern.
- **Alternatives considered**:
  - Parse or compile manifests directly inside workflow code: rejected because filesystem/network/object-store access and large payload handling are nondeterministic.
  - Collapse summary/index writing into one opaque activity result blob: rejected because plan, checkpoint, summary, and run index have different lifecycle and validation roles.

## Decision 5: Orchestrate each manifest node as a child `MoonMind.Run` workflow with immutable ingest lineage and request-cancel parent-close policy

- **Decision**: Every executable node becomes a child `MoonMind.Run` workflow start carrying immutable ingest lineage (`manifest_ingest_workflow_id`, `node_id`, authorization/requested-by context, relevant artifact refs), and child starts default to parent-close request-cancel behavior.
- **Rationale**: This matches the design doc, keeps the ingest workflow focused on orchestration, and reuses the existing v1 workflow type catalog rather than adding new root workflow categories.
- **Alternatives considered**:
  - Execute nodes as activities inside the ingest workflow: rejected because it would concentrate side effects and history growth in one workflow.
  - Add a second node-specific workflow type: rejected because current lifecycle/catalog work intentionally keeps the v1 root catalog limited.

## Decision 6: Expose editable manifest operations as workflow Updates, and keep node inspection artifact-backed and bounded

- **Decision**: Implement `UpdateManifest`, `SetConcurrency`, `Pause`, `Resume`, `CancelNodes`, and `RetryNodes` as manifest-ingest-specific workflow Updates, and serve node listings from bounded checkpoint/run-index views instead of in-memory full-graph payload dumps.
- **Rationale**: The repo already models generic update/signal APIs around Temporal execution control. Manifest ingest needs richer validation and acknowledgement semantics, which fit Update contracts better than signals or DB mutation.
- **Alternatives considered**:
  - Reuse generic `UpdateInputs` only: rejected because manifest-specific operations need semantic validation and partial-graph targeting.
  - Use direct DB mutation for pending-node edits: rejected because it would split the source of truth away from workflow state.

## Decision 7: Enforce bounded concurrency with config-driven defaults and artifact-backed Continue-As-New checkpoints

- **Decision**: Introduce dedicated manifest ingest settings for default concurrency, hard concurrency cap, ready-node scheduling batch size, and checkpoint/history thresholds; persist resumable orchestration state to checkpoint artifacts before Continue-As-New.
- **Rationale**: The current repo already exposes generic Continue-As-New thresholds, but manifest ingest needs additional controls tied to node fan-out and run-index growth. Checkpoint artifacts keep workflow history bounded while preserving restart correctness.
- **Alternatives considered**:
  - Keep only the existing phase-count threshold: rejected because manifest-scale pressure also depends on active child count and accumulated lineage/index state.
  - Let callers set unbounded concurrency: rejected because the feature must fail safe under load and Temporal limits.

## Decision 8: Use shared execution visibility for the ingest itself and a canonical run-index artifact for per-manifest lineage

- **Decision**: Keep the ingest execution listed through shared Temporal visibility fields (`mm_state`, `mm_entry`, owner metadata, bounded memo), and use `run_index_ref` as the only authoritative per-manifest child-run pagination source until a shared lineage Search Attribute is standardized.
- **Rationale**: The repo already has shared execution detail/list surfaces and artifact APIs. Reusing those prevents a split-source pagination model and matches the source document's warning against inventing ad hoc Search Attributes.
- **Alternatives considered**:
  - Add a manifest-specific lineage Search Attribute immediately: rejected because the shared visibility registry has not standardized one yet.
  - Build lineage pages from a local DB projection plus Temporal list reads: rejected because totals and cursors would drift across sources.

## Decision 9: Keep runtime selection at the activity/task-queue layer and do not encode business semantics into queue names

- **Decision**: Manifest ingest workflow code should decide what work is ready to execute, but actual runtime selection for artifact, LLM, sandbox, or integration work remains inside activity routing and child `MoonMind.Run` behavior driven by the existing queue topology (`mm.workflow`, `mm.activity.*`).
- **Rationale**: The current activity catalog and worker topology already define routing-only queue semantics. Manifest ingest must compose with that model instead of creating manifest-specific queues that encode business meaning.
- **Alternatives considered**:
  - Add `mm.manifest.*` queues for different node classes: rejected because it leaks business semantics into routing boundaries.
  - Branch the workflow type by runtime: rejected because runtime selection belongs below the workflow-type layer.

## Decision 10: Propagate authorization lineage explicitly and keep secrets out of workflow history, Search Attributes, and Memo

- **Decision**: Carry immutable requested-by and authorization lineage in workflow inputs, child workflow inputs, and bounded artifact metadata, while keeping manifest bodies, secrets, signed URLs, and high-cardinality payloads out of workflow history, Search Attributes, and Memo.
- **Rationale**: The repo already has artifact authorization and secret-redaction primitives, and the source design requires secrecy and access control as first-class runtime behavior.
- **Alternatives considered**:
  - Reconstruct authorization from registry rows or mutable API context later: rejected because child runs need immutable lineage and auditable ownership.
  - Store rich manifest/debug payloads in Memo for convenience: rejected because it violates size and secrecy constraints.

## Decision 11: Treat `BEST_EFFORT` with any failed node as terminal `failed`, not a pseudo-success state

- **Decision**: In v1, `BEST_EFFORT` ingest should continue scheduling eligible independent work after node failures, but if any node ends failed the ingest closes in canonical terminal state `failed` after writing a partial-failure summary.
- **Rationale**: This behavior is already clarified in `spec.md` and preserves compatibility with the shared lifecycle/close-status contract.
- **Alternatives considered**:
  - Close as `succeeded` with warnings: rejected because it masks failed work and breaks lifecycle consistency.
  - Introduce a new partial-success lifecycle state: rejected because the shared lifecycle contract intentionally keeps the canonical state set bounded.

## Decision 12: Runtime implementation mode remains the hard completion gate

- **Decision**: Planning, downstream tasks, and validation must all assume runtime implementation mode: production code plus automated tests are required deliverables.
- **Rationale**: The task objective, spec intent, and repo scope-validation tooling all require runtime code and tests. Docs-only completion would be non-compliant.
- **Alternatives considered**:
  - Stop at design artifacts: rejected because it would not satisfy the task objective.
  - Skip repository-standard validation: rejected because `./tools/test_unit.sh` is the required unit gate.
