# Research: Expose Distinct Full Retry Recovery Actions

Traceability: MM-632, source coverage IDs DESIGN-REQ-012 and DESIGN-REQ-014, spec FR-001 through FR-013.

## FR-001 / SCN-001 / SC-001 / DESIGN-REQ-001 - Distinct Recovery Action Exposure

Decision: `implemented_unverified`; add verification-first UI and API matrix coverage.
Evidence: `api_service/api/routers/executions.py` exposes `canEditForRerun`, `canRerun`, and `canResumeFromFailedStep`; `frontend/src/entrypoints/task-detail.tsx` renders Edit task, Rerun, and Resume from failed step as separate controls; existing tests cover selected happy paths in `frontend/src/entrypoints/task-detail.test.tsx` and `tests/unit/api/routers/test_executions.py`.
Rationale: The basic behavior exists, but MM-632 requires a matrix proving each action follows independent capability state. Current tests cover some combinations, not the full availability matrix.
Alternatives considered: Marking this implemented_verified was rejected because combined edge cases such as Edit-only, Rerun-only, Resume-only, and disabled-reason display are not all proven together.
Test implications: Unit UI and API tests first; integration coverage for at least one failed execution detail response.

## FR-002 / FR-003 / FR-007 / SCN-002 / SCN-003 / SC-002 / DESIGN-REQ-003

Decision: `implemented_unverified`; verify edited full retry opens from the authoritative snapshot, permits edits, creates a distinct new snapshot, and preserves the failed source.
Evidence: `frontend/src/lib/temporalTaskEditing.ts` maps `rerunExecutionId&mode=edit` to `edit-for-rerun`; `frontend/src/entrypoints/task-create.tsx` submits rerun edits through `RequestRerun`; `api_service/api/routers/executions.py` persists original task input snapshots; route tests include snapshot hydration.
Rationale: Existing behavior strongly suggests edited full retry is present, but MM-632 needs explicit proof that edited full retry is separate from exact Rerun and produces a distinct authoritative snapshot.
Alternatives considered: Treating the existing rerun edit route as complete was rejected because exact Rerun currently shares the same broad rerun update path.
Test implications: Unit tests for Create page edit-for-rerun loading and API snapshot persistence; integration coverage for source immutability.

## FR-004 / FR-005 / FR-006 / SCN-004 / SCN-005 / SC-003 / SC-004 / DESIGN-REQ-004 / DESIGN-REQ-007

Decision: `partial`; exact Rerun exists but must be tightened so it cannot carry edited task/input mutation payloads and cannot import Resume progress.
Evidence: `api_service/api/routers/executions.py` has a `/rerun` endpoint that reuses canonical parameters, and `TemporalExecutionService.update_execution(... update_name="RequestRerun")` supports manual rerun. However, `tests/unit/workflows/temporal/test_temporal_service.py::test_request_rerun_can_override_inputs_and_parameters` demonstrates rerun override support, and `frontend/src/entrypoints/task-create.tsx` can submit `parametersPatch` for `pageMode.mode === "rerun"`.
Rationale: MM-632 draws a clear product line: Rerun means exact full rerun with original input unchanged; Edit task is the mutation path. Current override-capable rerun behavior violates that distinction unless constrained.
Alternatives considered: Keeping override support as a convenience was rejected because it blurs exact Rerun with Edit task and conflicts with source design invariants.
Test implications: Unit tests should first prove exact Rerun rejects or omits mutation fields. Integration tests should prove exact Rerun starts from the beginning without `resumeSource`, `resumeCheckpointRef`, preserved steps, or edited task payload.

## FR-008 / DESIGN-REQ-002 - Capability Gating And Disabled Reasons

Decision: `implemented_unverified`; expand capability and disabled-reason matrix tests.
Evidence: `_build_action_capabilities()` gates recovery actions on workflow type, feature flags, state, task input snapshot ref, and resume checkpoint ref. Existing tests cover feature flag, workflow type, missing snapshot, and selected failed-resume behavior.
Rationale: The implementation is present, but matrix coverage should prove all three recovery actions are exposed only when their individual capability fields are true.
Alternatives considered: Relying on current route serialization tests was rejected because UI rendering and disabled-reason display also matter for the user-facing story.
Test implications: API unit tests plus Task Detail UI tests.

## FR-009 / DESIGN-REQ-005 - Resume Is Not An Edit Flow

Decision: `implemented_verified`; preserve existing behavior.
Evidence: `resume_execution_from_failed_step()` rejects task, instructions, steps, attachments, runtime, publish, branch, dependencies, model, effort, and artifact mutation fields. `test_failed_step_resume_request_rejects_edited_task_payload_fields` covers the rejection.
Rationale: This directly satisfies the in-scope Resume edit-prohibition requirement for MM-632.
Alternatives considered: Adding implementation work was rejected because the existing route-level guard and test already prove the behavior.
Test implications: None beyond final verification unless surrounding changes disturb this route.

## FR-010 / SCN-006 / SC-005 - Resume Unavailable Evidence

Decision: `partial`; missing checkpoint handling exists, but stale, unauthorized, and inconsistent evidence coverage must be added.
Evidence: `_build_action_capabilities()` reports `resume_checkpoint_missing`; `_hydrate_resume_checkpoint_payload()` and `resume_execution_from_failed_step()` surface `resume_not_available` errors. Existing tests hydrate a checkpoint and reject edited payload fields.
Rationale: The spec requires clear handling for missing, stale, unauthorized, and inconsistent durable progress evidence; current evidence is strongest for missing/happy-path checkpoint cases.
Alternatives considered: Deferring evidence validation to Resume-specific work was rejected for the operator-readable unavailability requirement in MM-632.
Test implications: Unit route/service tests and at least one hermetic integration boundary test.

## FR-011 / SCN-007 / SC-006 - Failed Source Immutability

Decision: `implemented_unverified`; add explicit immutability assertions.
Evidence: Temporal service tests show terminal source executions remain terminal when fresh rerun records are created, and Resume creates linked follow-up executions. Snapshot and artifact refs are persisted separately.
Rationale: The behavior appears aligned, but MM-632 requires proof that source state, snapshot, step ledger, artifacts, and checkpoints remain unchanged across all recovery action attempts.
Alternatives considered: Counting existing rerun service tests as sufficient was rejected because they do not cover all listed source artifacts.
Test implications: Unit and integration tests with before/after source state assertions.

## FR-012 - No Silent Intent Translation

Decision: `partial`; Resume rejects edited payloads, but exact Rerun still accepts mutation-style payloads.
Evidence: Resume route has explicit rejected-field handling; exact Rerun paths can currently pass `parametersPatch` or input artifact refs through the rerun update flow.
Rationale: The system must fail visibly instead of silently treating a mutation as exact Rerun or treating Resume as full retry. The Rerun side requires tightening.
Alternatives considered: Auto-converting exact Rerun with mutation payloads to Edit task was rejected because that is a silent semantic translation.
Test implications: Unit API and UI tests should assert visible failure or omission for exact Rerun mutations.

## FR-013 / SC-007 - Jira Traceability

Decision: `implemented_unverified`; preserve through all downstream artifacts and final verification.
Evidence: `specs/326-expose-distinct-full-retry-recovery-actions/spec.md` preserves the MM-632 Jira preset brief verbatim; this plan, research, data model, contract, and quickstart reference MM-632.
Rationale: Final verification still needs to confirm traceability after implementation and task artifacts are generated.
Alternatives considered: Storing only the issue key was rejected because final verification must compare against the original preset brief.
Test implications: Final verification and artifact traceability check.

## Test Tooling Strategy

Decision: Use separate unit and integration strategies.
Evidence: Repo instructions define `./tools/test_unit.sh`, `./tools/test_unit.sh --ui-args <path>`, and `./tools/test_integration.sh`; existing relevant tests live under `tests/unit/api/routers`, `tests/unit/workflows/temporal`, and `frontend/src/entrypoints`.
Rationale: API/service/UI behavior can be verified quickly in unit suites, while the story's end-to-end source immutability and no-progress-import claims need a boundary-level integration test.
Alternatives considered: UI-only testing was rejected because exact rerun and Resume evidence semantics are API/service contracts.
Test implications: Unit tests first, then hermetic integration_ci coverage if the repository has an appropriate execution route fixture.
