# Research: Canonical Remediation Submissions

## FR-001 Normal Run Creation Carries Remediation Metadata

Decision: implemented_verified.
Evidence: `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/api_service/api/routers/executions.py` preserves `task.remediation` in task-shaped submissions; `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/tests/unit/api/routers/test_executions.py` includes `test_create_task_shaped_execution_preserves_remediation_payload`.
Rationale: The router passes remediation metadata to `TemporalExecutionService.create_execution()` without converting it into dependencies or another payload shape.
Alternatives considered: Adding a new route-only payload shape was rejected because the canonical contract is nested task remediation metadata.
Test implications: Focused router unit coverage plus final verify.

## FR-002 Canonical Normalized Payload Preserves Remediation

Decision: implemented_verified.
Evidence: `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/moonmind/workflows/temporal/service.py` pins remediation target identity into `params["task"]["remediation"]`; service test asserts persisted parameters and workflow start payload contain `workflowId` and resolved `runId`.
Rationale: The canonical persisted source record and worker-bound start parameters receive the same pinned remediation target.
Alternatives considered: Persisting only the link row was rejected because final verification needs the canonical task payload to preserve remediation intent.
Test implications: Focused service unit coverage.

## FR-003 Directed Relationship Persistence

Decision: implemented_verified.
Evidence: `TemporalExecutionRemediationLink` in `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/api_service/db/models.py`; service test `test_create_execution_persists_remediation_link_and_supports_lookups`.
Rationale: The durable link is separate from dependencies and keyed by remediation workflow ID.
Alternatives considered: Scanning execution parameters for reverse lookup was rejected by existing model design and would not satisfy durable bidirectional lookup.
Test implications: Focused service persistence test.

## FR-004 Target Run Resolution

Decision: implemented_verified.
Evidence: `_validate_remediation_link()` resolves `target_record.run_id`, rejects mismatches, and writes `target_run_id`; tests cover omitted and supplied matching run IDs.
Rationale: Pinning before workflow start prevents silent target drift.
Alternatives considered: Resolving the latest run lazily at read time was rejected because the story requires pinned run identity at creation.
Test implications: Focused service tests for omitted, supplied, and mismatched run IDs.

## FR-005 Target Validation

Decision: implemented_verified.
Evidence: Service validation rejects missing target workflow ID, run ID used as workflow ID, missing target, unauthorized target, non-run target, and self-targeting.
Rationale: Fail-fast validation prevents null-target remediation runs and preserves authority boundaries.
Alternatives considered: Letting the workflow fail later was rejected because the story requires no workflow start for invalid submissions.
Test implications: Focused service validation tests.

## FR-006 Policy And Task-Run Validation

Decision: implemented_verified.
Evidence: Service validation rejects malformed taskRunIds, foreign taskRunIds, unsupported authorityMode, unsupported actionPolicyRef, and nested remediation targets; matching tests exist in `tests/unit/workflows/temporal/test_temporal_service.py`.
Rationale: These are create-time policy gates and are covered before link creation or workflow start.
Alternatives considered: Treating unknown authority/policy fields as defaults was rejected because unsupported values must fail closed.
Test implications: Focused service validation tests.

## FR-007 Bidirectional Relationship Visibility

Decision: implemented_verified.
Evidence: `TemporalExecutionRemediationLink` stores compact fields including status, active lock, latest action summary, outcome, and timestamps. `GET /api/executions/{workflow_id}/remediations` returns inbound/outbound summaries; router tests cover both directions.
Rationale: The visible relationship contract is already available through service lookups and router serialization.
Alternatives considered: Exposing only deep artifacts was rejected because Mission Control list/detail rendering needs compact read models.
Test implications: Focused router response tests plus service lookup tests.

## FR-008 No Dependency Gate

Decision: implemented_verified.
Evidence: Service test asserts `list_prerequisites(remediation.workflow_id) == []` after remediation creation.
Rationale: Remediation must start independently of target success.
Alternatives considered: Modeling remediation as `dependsOn` was rejected by the source design and current implementation.
Test implications: Focused service test.

## FR-009 Structured Failure Without Null Target

Decision: implemented_verified.
Evidence: Invalid service paths raise `TemporalExecutionValidationError` before commit/start; convenience route rejects malformed remediation objects with a structured 422 response.
Rationale: Validation happens before link insertion and workflow start, so invalid submissions cannot create null-target remediation tasks.
Alternatives considered: Deferred worker validation was rejected as too late for this contract.
Test implications: Focused router/service validation tests.

## FR-010 MM-617 Traceability

Decision: partial.
Evidence: `/work/agent_jobs/mm:51ba770c-9aeb-45d6-855a-72e6749d2c73/repo/specs/317-canonical-remediation-submissions/spec.md` and this plan preserve MM-617.
Rationale: Later tasks, verification, commit text, pull request metadata, and Jira handoff do not exist yet, so traceability must be carried forward.
Alternatives considered: Reusing the older `specs/226-canonical-remediation-submissions` artifact was rejected because it preserves MM-451 rather than MM-617.
Test implications: Final MoonSpec verification and PR checklist must include MM-617 traceability.

## DESIGN-REQ-007 Evidence Safety Constraint

Decision: implemented_verified for this story's scope.
Evidence: Relationship summaries expose `contextArtifactRef` and compact metadata, not raw storage paths, presigned URLs, or secret-bearing evidence bodies.
Rationale: Full evidence retrieval is out of scope, but link metadata remains safe and server-mediated.
Alternatives considered: Embedding evidence bodies in relationship responses was rejected by source design.
Test implications: Final verify confirms link contracts remain artifact-ref based.
