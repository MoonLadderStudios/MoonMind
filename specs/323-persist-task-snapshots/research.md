# Research: Persist Authoritative Task Snapshots

## Snapshot Persistence Boundary

Decision: Treat existing original task input snapshot artifact persistence as the authoritative storage path and keep this story focused on enforcing its use.
Evidence: `api_service/api/routers/executions.py` builds and persists `OriginalTaskInputSnapshot`; `moonmind/workflows/temporal/worker_runtime.py` persists child `MoonMind.Run` snapshots for Jira Orchestrate.
Rationale: The current code already writes compact artifact refs and records them in execution memo/artifact refs, matching the source design's artifact-backed durability model.
Alternatives considered: Adding a new database table was rejected because the spec and current implementation already use artifact-backed snapshots.
Test implications: Unit tests should verify snapshot-dependent action behavior; existing snapshot creation tests remain relevant.

## Edit And Rerun Reconstruction Policy

Decision: Remove parameter-derived fallback as an eligibility path for terminal edit/rerun actions when the authoritative task input snapshot is missing.
Evidence: `_build_action_capabilities()` currently computes `has_task_parameter_fallback` and allows `canEditForRerun`/`canRerun` without `task_input_snapshot_ref` when task parameters contain instructions or tool data.
Rationale: MM-629 explicitly requires edit, rerun, full retry, and resume reconstruction to use durable snapshots rather than lossy projections, and attachment-aware executions without reconstructible snapshots must be degraded explicitly.
Alternatives considered: Keeping fallback for text-only tasks was rejected because the spec requires the authored task snapshot as the authority for preset provenance, ordering, and dependencies, not just attachment refs.
Test implications: Update unit coverage so terminal executions without snapshots disable edit/rerun even if parameters look reconstructable.

## Resume Snapshot Requirement

Decision: Preserve the existing failed-step resume requirement that source executions have a task input snapshot ref and matching resume checkpoint payload.
Evidence: `TemporalExecutionService.create_failed_step_resume_execution()` raises when `task_input_snapshot_ref` is missing and validates checkpoint `taskInputSnapshotRef` against the source memo.
Rationale: This already satisfies MM-629's requirement that Resume uses original inputs unchanged and cannot become an edit flow.
Alternatives considered: No change needed.
Test implications: Existing unit tests cover required snapshot and checkpoint mismatches; final verification should cite them.

## Operator Evidence Surface

Decision: Keep `taskInputSnapshot` descriptor as the operator-visible source for authoritative, degraded, and unavailable reconstruction state, and align action disabled reasons with it.
Evidence: `_task_input_snapshot_descriptor_from_record()` returns `authoritative` when an artifact ref exists and `degraded_read_only` with fallback evidence refs when only input/plan refs exist.
Rationale: This gives operators bounded evidence without loading large artifact bodies while preventing unsafe edit/rerun entrypoints.
Alternatives considered: Hydrating artifacts during action serialization was rejected for performance and authorization complexity.
Test implications: Existing descriptor tests plus updated action tests are sufficient for this boundary.
