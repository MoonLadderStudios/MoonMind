# Research: Canonical Remediation Submissions

## Classification

Decision: Treat MM-451 as a single-story runtime feature request.
Evidence: `spec.md` (Input) contains one user story, one source design, one bounded submission/linkage slice, and no request for documentation-only work.
Rationale: The story can be independently validated by creating a remediation task and inspecting persisted payload/linkage behavior.
Alternatives considered: Broad design classification was rejected because the brief already selected one story from `docs/Tasks/TaskRemediation.md`; existing feature directory classification was rejected because no pre-existing `specs/*` artifact referenced MM-451.
Test implications: Use focused runtime unit tests, not docs-only checks.

## FR-001 / Canonical Create Acceptance

Decision: Mark implemented_verified.
Evidence: `api_service/api/routers/executions.py` copies `task_payload["remediation"]` into normalized task parameters; `tests/unit/api/routers/test_executions.py` includes task-shaped remediation preservation coverage.
Rationale: The router preserves the nested remediation object for the service instead of inventing a parallel top-level contract.
Alternatives considered: Adding a second create payload shape was rejected by the source design.
Test implications: Rerun router unit tests.

## FR-002 / Canonical Payload Storage

Decision: Mark implemented_verified.
Evidence: `moonmind/workflows/temporal/service.py` updates `params["task"]["remediation"]["target"]` with normalized workflow and run IDs before creating the source record; service tests assert the stored parameters.
Rationale: Durable payload storage remains under `initialParameters.task.remediation`.
Alternatives considered: Link-only persistence was rejected because the canonical source payload must retain remediation intent.
Test implications: Rerun service unit tests.

## FR-003 / Target Run Pinning

Decision: Mark implemented_verified.
Evidence: `_validate_remediation_link` loads the target `TemporalExecutionCanonicalRecord` and uses its current `run_id`; service tests cover omitted and supplied matching `target.runId`.
Rationale: The target source record is the local authoritative run identity for this slice.
Alternatives considered: Temporal describe lookup was rejected because the repo already persists current run identity locally.
Test implications: Rerun service unit tests.

## FR-004 / Directed Relationship Persistence

Decision: Mark implemented_verified.
Evidence: `api_service/db/models.py` defines `TemporalExecutionRemediationLink`; migrations `219_remediation_create_links.py`, `221_remediation_context_artifacts.py`, and `223_remediation_link_status_fields.py` provide durable link and compact metadata fields.
Rationale: Remediation is modeled as its own relationship, not as a dependency edge.
Alternatives considered: Reusing dependency tables was rejected by `docs/Tasks/TaskRemediation.md`.
Test implications: Rerun service unit tests for link persistence.

## FR-005 / Forward And Reverse Lookup

Decision: Mark implemented_verified.
Evidence: `TemporalExecutionService.list_remediation_targets` and `list_remediations_for_target` query outbound and inbound relationships; tests assert both directions.
Rationale: The story requires relationship inspection from both remediation and target sides.
Alternatives considered: Scanning execution parameters for reverse lookup was rejected as non-durable and inefficient.
Test implications: Rerun service unit tests.

## FR-006 / Structured Validation

Decision: Mark implemented_verified.
Evidence: `_validate_remediation_link` rejects missing target workflow ID, run IDs used as workflow IDs, self-targeting, missing targets, unauthorized targets, non-run targets, mismatched run IDs, malformed task run IDs, unsupported authority modes, unsupported action policy refs, and nested remediation targets; tests cover the same cases.
Rationale: Invalid remediation submissions must fail before workflow start and before link persistence.
Alternatives considered: Deferring validation to remediation runtime was rejected because create-time payload values affect authority and linkage semantics.
Test implications: Rerun service unit tests.

## FR-007 / Convenience Route

Decision: Mark implemented_verified.
Evidence: `api_service/api/routers/executions.py` exposes `POST /api/executions/{workflow_id}/remediation` and builds a task-shaped create payload with `task.remediation.target.workflowId`; router tests cover expansion, override behavior, and malformed remediation rejection.
Rationale: The convenience route is an input adapter over the canonical contract, not a second durable shape.
Alternatives considered: A route-specific persistence model was rejected by the source design.
Test implications: Rerun router unit tests.

## FR-008 / Dependency Separation

Decision: Mark implemented_verified.
Evidence: Service tests assert a remediation execution has no dependency prerequisites after creation.
Rationale: The target execution may be failed or incomplete; remediation must start independently of target success.
Alternatives considered: Modeling remediation as `dependsOn` was rejected by `docs/Tasks/TaskRemediation.md` section 5.2.
Test implications: Rerun service unit tests.
