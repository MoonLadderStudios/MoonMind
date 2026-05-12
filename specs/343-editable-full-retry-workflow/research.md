# Research: Editable Full Retry Workflow

Traceability: MM-644, `spec.md` FR-001 through FR-012.

## Setup Limitation

Decision: planning proceeds from `.specify/feature.json` because `.specify/scripts/bash/setup-plan.sh --json` rejected the managed branch name.
Evidence: setup script returned `ERROR: Not on a feature branch. Current branch: change-jira-issue-mm-644-to-status-in-pr-2d46bf18`; `.specify/feature.json` points to `specs/343-editable-full-retry-workflow`.
Rationale: The feature directory and spec already exist and are the active MoonSpec artifacts for MM-644.
Alternatives considered: Rename the branch; rejected because this managed step is scoped to planning artifacts only.
Test implications: none beyond final artifact verification.

## FR-001 / SCN-007 / SC-004

Decision: partial; verify or tighten action eligibility so Edit task is offered only when an authoritative snapshot exists and is readable by the current user.
Evidence: `api_service/api/routers/executions.py` `_build_action_capabilities()` requires a snapshot ref before `canEditForRerun`; `_task_input_snapshot_descriptor_from_record()` reports unavailable/degraded snapshot state; Create page later reads the artifact and can fail.
Rationale: Snapshot presence is checked, but current evidence does not prove action availability accounts for user-specific artifact readability before showing Edit task.
Alternatives considered: Accept read failure only after navigation; rejected because FR-001 and FR-011 require eligibility or unavailable reason before starting a retry.
Test implications: API unit tests for missing/unreadable/unauthorized snapshot states and UI tests for operator-readable blocked edit-for-rerun.

## FR-002 / DESIGN-REQ-001

Decision: implemented_unverified; add UI verification for edit-for-rerun route and snapshot hydration.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` maps `?rerunExecutionId=<id>&mode=edit` to intent `edit-for-rerun`; `frontend/src/entrypoints/task-create.tsx` checks `actions.canEditForRerun` and reads `taskInputSnapshot.artifactRef` when reconstruction mode is authoritative.
Rationale: The route and hydration code exist, but tests currently emphasize rerun mode and not the exact edit-for-rerun path.
Alternatives considered: Treat exact-rerun tests as sufficient; rejected because edit-for-rerun has distinct capability and copy requirements.
Test implications: UI unit test using `frontend/src/entrypoints/task-create.test.tsx`.

## FR-003 / FR-004 / DESIGN-REQ-002

Decision: implemented_unverified; prove edit-for-rerun permits representative authoring edits and normal validation.
Evidence: The Create page uses shared authoring state and submit validation for create/edit/rerun modes; `RequestRerun` receives `parametersPatch` when form state changes.
Rationale: Shared code likely satisfies this behavior, but MM-644 needs explicit coverage for edit-for-rerun rather than exact rerun.
Alternatives considered: Add separate validation rules for edit-for-rerun; rejected because the spec requires normal authoring validation.
Test implications: UI unit tests for edited instructions/steps and at least one invalid publish/branch or required-field case.

## FR-005 / FR-007 / DESIGN-REQ-003

Decision: implemented_unverified; verify changed edit-for-rerun from a terminal failed execution creates a new from-beginning execution.
Evidence: `TemporalExecutionService._create_fresh_rerun_execution()` creates a new execution when terminal workflows receive `RequestRerun`; `_full_rerun_parameters()` strips taskRunId and dependency carryover.
Rationale: Terminal rerun behavior exists, but tests should cover the edited-full-retry path with changed payload, not only exact rerun.
Alternatives considered: Use Continue-As-New on the source record for terminal executions; rejected because the current service path already creates a distinct record for terminal updates and this better preserves source immutability.
Test implications: hermetic integration test in `tests/integration/temporal/`.

## FR-006 / DESIGN-REQ-004

Decision: partial; prove the edited retry receives its own authoritative task input snapshot.
Evidence: `api_service/api/routers/executions.py` persists a task input snapshot after task editing updates with source kind `rerun`; snapshot payload source can include source workflow/run IDs.
Rationale: The helper exists, but no MM-644 evidence proves the new edited retry record receives a fresh snapshot with edited content and lineage instead of only reusing the source input.
Alternatives considered: Reuse the source snapshot for edited retry; rejected because the spec requires the edited execution to get its own snapshot.
Test implications: integration test with artifact service fixture or route-level unit test proving snapshot metadata and content.

## FR-008 / DESIGN-REQ-005

Decision: implemented_unverified; add source immutability verification specific to edited full retry.
Evidence: Existing exact full rerun integration tests assert the source record remains terminal; fresh rerun creates a new record rather than mutating source execution identity.
Rationale: Source execution immutability must include snapshot, ledger/progress refs, artifacts, and checkpoint refs for edited full retry.
Alternatives considered: Rely on exact rerun evidence; rejected because changed edited retry adds snapshot and parameter patch behavior.
Test implications: integration assertions for source record state, input refs, artifact refs, memo snapshot ref, and progress/checkpoint metadata before and after edited retry.

## FR-009 / DESIGN-REQ-006

Decision: implemented_unverified; add edited full retry regression for no completed progress import.
Evidence: `TemporalExecutionService._strip_resume_reference_parameters()` removes `resumeSource`, `resumeCheckpointRef`, `preservedSteps`, `completedSteps`, and task `recovery`/`resume`; existing tests cover exact rerun and an UpdateInputs recovery case.
Rationale: MM-644 requires the edited full retry path specifically to avoid Resume or completed-progress carryover.
Alternatives considered: Add UI-only assertion; rejected because the critical behavior is in service/API normalization.
Test implications: service unit and hermetic integration tests with Resume-shaped source metadata and changed edited retry payload.

## FR-010

Decision: partial; make edited-full-retry provenance explicit at the accepted boundary.
Evidence: `TaskRecoveryProvenance` supports `edited_full_retry`; current rerun path records top-level `rerunSource` and snapshot source metadata but does not clearly distinguish exact full rerun from edited full retry in canonical recovery provenance.
Rationale: The spec requires recovery kind and pinned source execution identity for audit. Existing generic rerun metadata is close but not sufficient to prove edited intent.
Alternatives considered: Treat all `RequestRerun` updates as `manual_rerun`; rejected because MM-644 requires distinguishing changed edited full retry from exact full rerun.
Test implications: task-contract/unit test for `edited_full_retry`, API/service test for provenance derivation, and integration test for persisted provenance.

## FR-011

Decision: partial; standardize unavailable reasons for missing, unreadable, unauthorized, or insufficient snapshots.
Evidence: Missing snapshot disables actions with `original_task_input_snapshot_missing`; Create page surfaces load errors from execution detail or artifact download.
Rationale: Operators need a bounded reason before retry starts, and read failures should not silently fall through to degraded editable state.
Alternatives considered: Let browser artifact download errors be the only feedback; rejected because the spec asks the system to prevent edited full retry from starting with operator-readable reason.
Test implications: API unit + UI unit tests for unavailable action and artifact read failure paths.

## FR-012 / SC-005

Decision: implemented_unverified; preserve MM-644 traceability through all artifacts.
Evidence: `spec.md` preserves MM-644 and the original Jira preset brief; this research preserves MM-644.
Rationale: Final verification compares implementation and evidence to the original preset brief.
Alternatives considered: Preserve only the issue key; rejected because the original brief is required for downstream verification.
Test implications: final MoonSpec verification.

## Test Strategy

Decision: use separate unit, UI unit, and hermetic integration strategies.
Evidence: Repo instructions require `./tools/test_unit.sh` for unit verification and `./tools/test_integration.sh` for `integration_ci`; frontend focused tests can run through `./tools/test_unit.sh --ui-args`.
Rationale: Editable full retry crosses Task Detail capability rendering, Create page authoring and snapshot hydration, API update handling, artifact snapshot persistence, and Temporal execution creation.
Alternatives considered: Unit-only coverage; rejected because source immutability and new execution/snapshot behavior are boundary-sensitive.
Test implications: unit + UI + hermetic integration, followed by full `./tools/test_unit.sh` and targeted/full integration as feasible.
