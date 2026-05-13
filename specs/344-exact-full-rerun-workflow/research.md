# Research: Exact Full Rerun Workflow

## FR-001 / FR-006 / SCN-001 / SC-001

Decision: Partial; exact Rerun needs a direct action path that does not open the task authoring form.

Evidence: `frontend/src/entrypoints/task-detail.tsx` builds Rerun links with `taskRerunHref()`, which points to `/tasks/new?rerunExecutionId=...`. `frontend/src/entrypoints/task-create.tsx` renders and submits a Rerun Task page for that mode. Existing tests in `frontend/src/entrypoints/task-detail.test.tsx` and `frontend/src/entrypoints/task-create.test.tsx` assert those links and form submissions.

Rationale: MM-645 explicitly says Rerun never opens an authoring form. Current behavior supports exact no-mutation submission only after entering the create/rerun page, so it does not satisfy the operator flow.

Alternatives considered: Keeping the form and disabling editing was rejected because the acceptance criterion says Rerun never opens an authoring form; editable retry is a separate flow.

Test implications: Add frontend unit tests for a direct Rerun action from detail and backend integration/API tests proving the request creates a new execution.

## FR-002 / FR-008 / SCN-002 / SC-002 / DESIGN-REQ-003

Decision: Partial; authoritative snapshot infrastructure exists, but exact rerun must prove unchanged source snapshot reuse.

Evidence: `api_service/api/routers/executions.py` exposes task input snapshot descriptors, persists original task input snapshots, and disables task editing actions when the snapshot is missing. `moonmind/workflows/tasks/task_contract.py` builds authoritative task input snapshots. Tests in `tests/unit/api/routers/test_executions.py` cover snapshot persistence and authored field preservation.

Rationale: Current rerun paths derive parameters from canonical parameters and persist a new rerun snapshot. MM-645 requires exact rerun to reuse the original task input snapshot unchanged as execution input; planning should verify and tighten source snapshot identity/content.

Alternatives considered: Treating newly persisted rerun snapshots as sufficient was rejected because the story requires unchanged source snapshot reuse, not reconstruction from mutable projections.

Test implications: Unit and integration tests should compare the exact rerun input source to the original authoritative snapshot and cover attachments/preset metadata where available.

## FR-003 / FR-004 / SCN-003 / SC-003 / DESIGN-REQ-001

Decision: Missing for exact no-mutation rerun; add server-derived `exact_full_rerun` provenance with pinned source workflow/run IDs.

Evidence: `frontend/src/entrypoints/task-create.tsx` sends an exact `RequestRerun` with no `parametersPatch` when the form is unchanged. `moonmind/workflows/temporal/service.py` only carries recovery provenance when `_full_retry_recovery_from_patch()` finds a recovery object in a patch. With no patch, `_apply_request_rerun()` calls `_full_rerun_parameters(record.parameters)` without recovery provenance.

Rationale: Exact rerun cannot rely on the client to submit provenance if MM-645 removes the authoring-form path. The backend already has the source record and should pin source workflow/run identity.

Alternatives considered: Making the UI send a patch containing `exact_full_rerun` was rejected as the primary mechanism because exact rerun should not send mutable task payload fields and because source identity must be authoritative server-side.

Test implications: Add service and route tests for exact rerun provenance, including rejection or blocked behavior when source run identity is missing.

## FR-005 / SCN-004

Decision: Implemented unverified; from-beginning execution behavior appears present but needs focused MM-645 proof.

Evidence: `moonmind/workflows/temporal/service.py` creates a fresh rerun execution and `_full_rerun_parameters()` removes task run IDs and dependency carryover. `api_service/api/routers/executions.py` also exposes a legacy rerun endpoint that creates a new execution.

Rationale: New execution creation suggests from-beginning behavior, but MM-645 requires explicit validation that preparation, prompt composition, planning or plan hydration, and all steps are not skipped or treated as preserved progress.

Alternatives considered: Marking verified from existing rerun tests was rejected because current tests are split across adjacent features and do not prove the complete exact-rerun path.

Test implications: Add hermetic integration coverage that inspects new execution parameters/projections for a fresh full pipeline run.

## FR-007 / SCN-005 / SC-004 / DESIGN-REQ-002

Decision: Implemented unverified; resume/progress cleanup exists but exact rerun needs focused coverage.

Evidence: `_strip_resume_reference_parameters()` in `moonmind/workflows/temporal/service.py` removes resume-related top-level fields and task resume/recovery blocks. Existing service tests cover resume cleanup in update and resume contexts.

Rationale: The helper likely prevents resume progress import, but exact full rerun should have dedicated proof that completed steps, preserved outputs, resume checkpoint refs, and resume source are absent.

Alternatives considered: Relying on helper-level coverage only was rejected because MM-645 is a user-facing recovery workflow and needs boundary-level evidence.

Test implications: Add unit tests for `_full_rerun_parameters()` and integration tests through the API/update path.

## FR-009

Decision: Implemented verified; preserve existing degraded/blocked behavior for missing snapshots.

Evidence: `api_service/api/routers/executions.py` disables edit/rerun actions when `task_input_snapshot_ref` is absent and reports `original_task_input_snapshot_missing`. Tests in `tests/unit/api/routers/test_executions.py` cover this behavior, including `test_temporal_task_editing_actions_require_original_snapshot` and related terminal fallback rejection tests.

Rationale: The missing-snapshot guard already matches the fail-closed behavior requested by MM-645. Direct Rerun should reuse the same action eligibility.

Alternatives considered: Creating exact rerun from canonical parameters when a snapshot is missing was rejected because it can silently lose authored fields.

Test implications: Final verify plus regression coverage around the new direct Rerun route.

## FR-010 / SC-005

Decision: Implemented unverified; traceability starts in `spec.md` and must continue through later artifacts.

Evidence: `specs/344-exact-full-rerun-workflow/spec.md` preserves Jira issue `MM-645` and the original preset brief verbatim in the input field.

Rationale: This plan step continues traceability, but tasks, implementation notes, verification, commit text, and pull request metadata do not exist yet.

Alternatives considered: Treating spec preservation alone as complete was rejected because FR-010 explicitly names downstream artifacts.

Test implications: Final MoonSpec verification should audit all artifacts for `MM-645`.

## Technical Approach

Decision: Implement exact Rerun as a direct recovery action using existing task editing/update and execution service boundaries.

Evidence: Existing contracts include `canRerun`, `RequestRerun`, task input snapshot descriptors, recovery provenance models, and rerun execution creation.

Rationale: This keeps the change within current Mission Control and Temporal execution boundaries while separating exact rerun from editable retry.

Alternatives considered: Adding a new workflow type or new persistence table was rejected because existing update/route/service contracts are sufficient.

Test implications: Unit tests for payload/provenance helpers, frontend action behavior, and API route serialization; hermetic integration tests for the end-to-end exact rerun creation path.

## Test Tooling

Decision: Use `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for hermetic integration_ci verification.

Evidence: Repo instructions define those as required verification runners. Existing frontend tests use Vitest through the unit runner, and Python tests use pytest.

Rationale: MM-645 crosses frontend, API, and Temporal service boundaries, so both unit and hermetic integration coverage are required.

Alternatives considered: Provider verification was rejected because this story uses local execution contracts and no third-party provider credentials.

Test implications: During iteration, focused `pytest` and `npm run ui:test -- <path>` may be used, but final verification should run the required wrappers.
