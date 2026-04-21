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
