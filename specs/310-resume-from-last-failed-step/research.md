# Research: Resume from Last Failed Step

## Setup Script Limitation

Decision: Use `.specify/feature.json` as the active feature locator for planning.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` failed with `ERROR: Not on a feature branch. Current branch: change-jira-issue-mm-602-to-status-in-pr-5652b805`; `.specify/feature.json` points to `specs/310-resume-from-last-failed-step`.
Rationale: The managed branch naming guard blocks the script, but the feature directory and spec already exist and are unambiguous.
Alternatives considered: Renaming the branch was rejected because this managed step is planning-only and should not mutate branch state.
Test implications: None beyond verifying generated artifacts exist.

## FR-001 Failed-Step Resume Capability

Decision: Missing. Add a distinct `canResumeFromFailedStep` capability and disabled reasons.
Evidence: `moonmind/schemas/temporal_models.py` defines `ExecutionActionCapabilityModel` with `canResume` but no `canResumeFromFailedStep`; `api_service/api/routers/executions.py` `_build_action_capabilities()` only maps lifecycle resume and rerun/edit actions.
Rationale: Existing `canResume` is paused/awaiting lifecycle resume and cannot safely represent failed-step Resume.
Alternatives considered: Reusing `canResume` was rejected because MM-602 requires failed-step Resume to be distinct from paused-task lifecycle Resume.
Test implications: Unit tests for schema/action serialization and API tests for eligible/ineligible failed executions; frontend unit tests for distinct action rendering.

## FR-002 Linked Follow-Up Execution

Decision: Missing. Add a trusted resume command path that creates a new linked execution and leaves the source unchanged.
Evidence: `api_service/api/routers/executions.py` has `/rerun` and update-based `RequestRerun`; no `resume-from-failed-step` route or service path was found.
Rationale: Resume semantics differ from full rerun because they preserve prior step work and start at the failed step.
Alternatives considered: Extending `RequestRerun` was rejected because the source docs require a distinct command surface.
Test implications: API/Temporal service unit tests plus contract tests for response shape and source immutability.

## FR-003 Source Identity Pinning

Decision: Partial. Existing rerun snapshot metadata uses source workflow/run IDs, but failed-step Resume needs a dedicated `resumeSource` contract.
Evidence: `api_service/api/routers/executions.py` persists snapshot metadata with `sourceWorkflowId` and `sourceRunId`; `moonmind/workflows/temporal/service.py` builds `rerunSource` for terminal reruns.
Rationale: Resume validation must pin both source identity values so checkpoint restoration cannot drift.
Alternatives considered: Using only workflow ID was rejected because Continue-As-New and rerun semantics rotate run identity.
Test implications: Unit tests for missing/blank source run rejection and response provenance.

## FR-004 Original Input Snapshot Unchanged

Decision: Partial. Snapshot persistence exists, but Resume-specific edited payload rejection does not.
Evidence: `api_service/api/routers/executions.py` persists original task input snapshots and task editing/rerun paths exist; no Resume request schema exists to reject edited task values.
Rationale: Resume must not become an edit-for-rerun path.
Alternatives considered: Allowing selected metadata overrides was rejected for v1 except bounded operator metadata/idempotency because the spec forbids edited task payload values.
Test implications: Contract tests and service unit tests for rejecting edited instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, and model settings.

## FR-005 Resume Checkpoint Evidence

Decision: Partial. Step ledger and artifact ref slots exist, but no resume checkpoint contract exists.
Evidence: `moonmind/schemas/temporal_models.py` defines step ledger rows with refs/artifacts; `moonmind/workflows/temporal/step_ledger.py` builds snapshots; no resume checkpoint model or content type was found.
Rationale: Checkpoint evidence is the core eligibility boundary and must stay compact in workflow history.
Alternatives considered: Embedding complete checkpoint bodies in workflow payloads was rejected by constitution and source docs.
Test implications: Unit tests for checkpoint model validation and artifact ref handling; integration tests for missing/incomplete refs.

## FR-006 Resume Validation

Decision: Missing. Add a validation service before execution starts new work.
Evidence: No source state, checkpoint, plan, output, prepared input, or workspace restoration validation for failed-step Resume exists.
Rationale: Resume must fail before any new step execution when validation fails.
Alternatives considered: Letting `MoonMind.Run` discover invalid state after start was rejected because the spec requires pre-execution failure.
Test implications: Unit tests for every validation failure and at least one integration/boundary success path.

## FR-007 Explicit Failure Without Full Rerun Fallback

Decision: Missing. Add explicit resume failure outcomes and avoid fallback to full rerun.
Evidence: Existing rerun paths create fresh executions; no resume-specific failure class or response exists.
Rationale: Silent fallback would violate the operator trust contract and could rerun expensive completed work.
Alternatives considered: Falling back to full rerun on checkpoint failure was rejected by source docs and spec.
Test implications: Unit/integration tests for invalid checkpoint, unauthorized refs, stale plan, and restore failure.

## FR-008 Preserved Step Materialization

Decision: Missing. Add preserved-step status/provenance to resumed execution progress.
Evidence: Step ledger rows support statuses such as pending/running/succeeded/failed/skipped/canceled, but no preserved/reused status or source provenance slots exist.
Rationale: Operators must see prior steps as reused from the source run, not freshly executed.
Alternatives considered: Marking preserved steps as `succeeded` without provenance was rejected because it hides source-run reuse.
Test implications: Workflow boundary tests and API/frontend rendering tests.

## FR-009 Task Details UX

Decision: Partial. Task detail UI has lifecycle Resume and rerun/edit controls, but no failed-step Resume action or related-run label.
Evidence: `frontend/src/entrypoints/task-detail.tsx` parses `actions.canResume` and sends Temporal `Resume`; rerun/edit links exist; no `canResumeFromFailedStep` parsing or `Resumed from failed step` UI was found.
Rationale: The operator must understand Resume as a failed-step recovery path, separate from pause/resume lifecycle.
Alternatives considered: Re-labeling existing `Resume` was rejected because it would conflate unrelated states.
Test implications: Frontend unit tests for action visibility, disabled reasons, confirmation/success copy, and related-run labels.

## FR-010 Operator Diagnostics

Decision: Partial. Step ledger and progress query surfaces exist, but resume diagnostics do not.
Evidence: `api_service/api/routers/executions.py` exposes `/steps`; `MoonMind.Run` exposes `get_step_ledger`; no checkpoint validation or preserved-step diagnostics are exposed.
Rationale: Operators should diagnose why Resume is unavailable or failed without reading worker internals.
Alternatives considered: Logging-only diagnostics were rejected because Mission Control is the primary operator surface.
Test implications: API/unit tests for disabled reasons and diagnostics; integration tests for surfaced checkpoint validation failure.

## FR-011 Boundary Coverage

Decision: Missing. Add the required coverage matrix.
Evidence: Existing tests cover snapshots, rerun, step ledger, and lifecycle Resume; search found no `resume_from_failed_step`, `canResumeFromFailedStep`, `resumeCheckpoint`, or `Resumed from failed step` tests.
Rationale: Constitution principle IX requires boundary-level coverage for compatibility-sensitive orchestration behavior.
Alternatives considered: Isolated unit tests only were rejected because workflow/activity and adapter boundaries are the main risk.
Test implications: Unit, contract/API, workflow boundary, and frontend unit tests.

## FR-012 Traceability

Decision: Implemented for specify/plan; preserve through later artifacts.
Evidence: `specs/310-resume-from-last-failed-step/spec.md` preserves MM-602 and the canonical Jira preset brief; this plan also preserves MM-602.
Rationale: Final verification and PR metadata must compare implementation against the original Jira input.
Alternatives considered: Referencing only the Jira key was rejected because verification requires the original brief.
Test implications: Final verification only.

## Storage and Relationship Strategy

Decision: Store compact resume source metadata on the resumed execution and checkpoint evidence as artifact refs first; derive related runs from existing execution records where practical.
Evidence: Existing execution records carry parameters/memo/search attributes, task input snapshot refs, and artifact refs; `execution_remediation_links` is remediation-specific and should not be overloaded without a generic relationship refactor.
Rationale: This keeps large checkpoint evidence out of workflow history and avoids a new table unless reverse related-run queries cannot be implemented cleanly.
Alternatives considered: New generic execution relationship table was deferred to implementation only if existing records cannot satisfy source/resumed detail queries without inefficient or brittle JSON scans.
Test implications: API integration tests must cover source-to-resumed and resumed-to-source related-run views.

## Unit and Integration Test Strategy

Decision: Use focused red-first unit tests for schema/service/router/UI behavior and integration or contract tests for API/workflow boundaries.
Evidence: Repo guidance requires `./tools/test_unit.sh`; frontend tests use Vitest through `npm run ui:test` or `./tools/test_unit.sh --ui-args`; integration_ci uses `./tools/test_integration.sh`.
Rationale: Resume changes span compatibility-sensitive workflow/activity/update payloads and Mission Control UX.
Alternatives considered: Manual verification only was rejected by constitution and MM-602 acceptance criteria.
Test implications: Unit tests should be run first for targeted files, then the full unit runner and relevant integration_ci suite before final verification.
