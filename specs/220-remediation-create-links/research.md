# Research: Remediation Create Links

## FR-001 / Task Create Normalization

Decision: Preserve `payload.task.remediation` as a nested task field in the task-shaped create router before calling `TemporalExecutionService.create_execution`.
Evidence: `api_service/api/routers/executions.py` manually builds `normalized_task_for_planner` and only copies known fields; direct `CreateExecutionRequest.initialParameters` already passes arbitrary nested task fields.
Rationale: The canonical create contract in `docs/Tasks/TaskRemediation.md` is `initialParameters.task.remediation`; task-shaped submission must normalize into that same object.
Alternatives considered: Adding a parallel top-level remediation field was rejected because canonical docs require nested task remediation.
Test implications: Router unit test for task-shaped submit preserving remediation into service call.

## FR-002 / Target Run Pinning

Decision: Resolve the target from `temporal_execution_sources` during create and persist the target's current `run_id` when the request omits `target.runId`.
Evidence: Dependency validation already uses source records as the durable lookup surface in `TemporalExecutionService._load_dependency_targets`.
Rationale: Current source records are the available authoritative create-time identity store.
Alternatives considered: Temporal describe lookup was rejected because the service already persists current run identity locally and tests should stay hermetic.
Test implications: Service unit test asserts link target run ID equals the existing target run ID.

## FR-003 / Link Storage

Decision: Add an `execution_remediation_links` table with `remediation_workflow_id` as primary key and indexed `target_workflow_id`.
Evidence: `execution_dependencies` is the closest relationship table but remediation is explicitly not a dependency.
Rationale: One remediation task targets one execution in this slice; the primary key enforces that invariant.
Alternatives considered: Storing links only inside `parameters.task.remediation` was rejected because reverse lookup would require scanning executions.
Test implications: Service unit tests query outbound and inbound links.

## FR-004 / Validation

Decision: Reject missing `target.workflowId`, non-`mm:` workflow IDs, run ID identifiers, missing targets, non-run targets, unauthorized owner scope, and mismatched supplied target run IDs.
Evidence: Dependency validation already rejects run IDs and owner mismatch for relationship references.
Rationale: This keeps remediation target validation consistent with existing execution relationship policy.
Alternatives considered: Allowing historical target run IDs was rejected because no durable historical run table is available in this slice.
Test implications: Service unit tests cover missing, run-ID, missing target, non-run target, and mismatched run ID.

## FR-007 / Dependency Separation

Decision: Do not write `execution_dependencies` rows for remediation links and do not add `dependsOn`.
Evidence: `docs/Tasks/TaskRemediation.md` section 5.2 says remediation is a relationship, not a dependency.
Rationale: Remediation often starts because the target failed, so dependency wait semantics are inappropriate.
Alternatives considered: Reusing `dependsOn` was rejected by the source design.
Test implications: Unit test asserts remediation creation has no prerequisites.

## FR-008 / Authority Mode Validation

Decision: Validate `task.remediation.authorityMode` against the desired-state modes `observe_only`, `approval_gated`, and `admin_auto` during create-time service validation.
Evidence: `docs/Tasks/TaskRemediation.md` section 7.3 defines the allowed authority modes, and `moonmind/workflows/temporal/service.py` owns remediation create validation before workflow start.
Rationale: Unsupported authority modes affect privilege semantics and must fail before a remediation link or workflow starts.
Alternatives considered: Deferring authority-mode validation to later action execution was rejected because the canonical create contract requires structured rejection at create time.
Test implications: Service unit test for unsupported `authorityMode`.

## FR-009 / Action Policy Compatibility

Decision: Validate `task.remediation.actionPolicyRef` against the trusted action policy references available in this slice, currently `admin_healer_default`.
Evidence: `docs/Tasks/TaskRemediation.md` section 7.4 requires action policy compatibility validation, while no broader persisted action policy catalog exists in this story.
Rationale: A narrow allowlist keeps create-time behavior deterministic without inventing a new policy storage model.
Alternatives considered: Accepting arbitrary policy refs was rejected because it would silently defer an incompatible privilege request; adding a full policy registry was rejected as broader action-execution scope.
Test implications: Service unit test for unsupported `actionPolicyRef`.

## FR-010 / Task Run ID Shape Validation

Decision: Validate `task.remediation.target.taskRunIds` as a list of non-empty strings during create-time service validation.
Evidence: `docs/Tasks/TaskRemediation.md` section 7.3 defines `target.taskRunIds[]`; `moonmind/workflows/temporal/remediation_context.py` later normalizes bounded task-run IDs for context artifacts.
Rationale: This slice can enforce the durable payload shape without coupling create-time validation to later evidence lookup internals.
Alternatives considered: Rejecting unknown task-run IDs by resolving managed-run records was rejected because not every target has a persisted managed-run binding at create time and later context tooling performs evidence scoping.
Test implications: Service unit test for malformed `target.taskRunIds`.

## FR-011 / Nested Remediation Guard

Decision: Reject remediation tasks that target an execution whose canonical parameters already contain `task.remediation`.
Evidence: `docs/Tasks/TaskRemediation.md` section 6 states nested remediation is off by default.
Rationale: Disallowing nested remediation at create time prevents unbounded self-healing loops until policy explicitly supports them.
Alternatives considered: Allowing nested remediation under `admin_auto` was rejected because no loop-prevention policy surface exists in this slice.
Test implications: Service unit test for nested remediation target rejection.

## FR-012 / Convenience Route Expansion

Decision: Add `POST /api/executions/{workflowId}/remediation` as a control-plane convenience route that builds the same task-shaped create contract as `POST /api/executions`.
Evidence: `docs/Tasks/TaskRemediation.md` section 7.5 allows a convenience route only when it expands into the canonical execution create contract.
Rationale: The route improves submission ergonomics without adding a second durable payload shape.
Alternatives considered: Persisting a route-specific remediation payload was rejected because `initialParameters.task.remediation` is the canonical durable contract.
Test implications: Router unit test for expansion into `initial_parameters.task.remediation`.

## DESIGN-REQ-024 / Compact Link Metadata

Decision: Add nullable lock/action/outcome fields to the remediation link model so canonical link data remains upstream of later read models.
Evidence: `docs/Tasks/TaskRemediation.md` sections 8.3 and 8.4 require durable linkage to support current status, lock holder, latest action summary, final outcome, and downstream rendering.
Rationale: Nullable compact fields preserve the read-model foundation without implementing action execution, locks, or UI rendering in this story.
Alternatives considered: Deferring all fields until a later UI/API slice was rejected because the MM-431 preset brief includes link support for lock/action/outcome metadata.
Test implications: Service unit test verifies newly created links expose the compact metadata fields as unset until later lifecycle updates.
