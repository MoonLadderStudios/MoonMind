# Research: Compile-Time Preset Composition With Provenance Preservation

## FR-001 / DESIGN-REQ-010

Decision: Treat compile-time recursive composition as implemented and verified by existing catalog expansion behavior.
Evidence: `api_service/services/task_templates/catalog.py` builds recursive composition and authored preset metadata; `tests/unit/api/test_task_step_templates_service.py` asserts expansion output, composition metadata, and authored presets.
Rationale: The Jira brief requires control-plane resolution before execution submission, which is already represented by catalog expansion and submission normalization paths.
Alternatives considered: Add a new compiler service. Rejected because existing catalog expansion is the established control-plane boundary and is already covered by focused tests.
Test implications: Rerun catalog unit tests and focused task-shaped integration coverage.

## FR-002

Decision: Treat include-tree validation as implemented and verified by current unit coverage.
Evidence: `tests/unit/api/test_task_step_templates_service.py` includes cases for missing targets, unavailable presets, cycles, duplicate aliases, incompatible input mappings, and scope/authorization failures.
Rationale: The story needs explicit failure before execution finalization; catalog validation is the earliest boundary that can stop invalid compositions.
Alternatives considered: Validate only in the API route. Rejected because worker and frontend paths also rely on catalog compilation semantics.
Test implications: Rerun catalog unit tests.

## FR-003 / DESIGN-REQ-011

Decision: Treat deterministic flattened order and stable submitted identity as implemented and verified.
Evidence: `tests/unit/api/test_task_step_templates_service.py`, `tests/unit/api/routers/test_executions.py`, `frontend/src/entrypoints/task-create.test.tsx`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py` assert final order and source metadata.
Rationale: Deterministic order is a submitted contract invariant and is covered across catalog, Create page, API, and integration boundaries.
Alternatives considered: Add a separate ordering manifest. Rejected because final submitted step order plus compact provenance is sufficient.
Test implications: Rerun focused backend, frontend, and integration tests.

## FR-004 / FR-006 / DESIGN-REQ-011

Decision: Treat provenance durability as implemented and verified by task contract, route, frontend, and integration tests.
Evidence: `moonmind/workflows/tasks/task_contract.py` models `authoredPresets` and `steps[].source`; tests assert include path, input mapping, preset slug/version, original step ID, and detached state preservation.
Rationale: The submitted task snapshot is the durable source for audit, reconstruction, rerun, and diagnostics after catalog drift.
Alternatives considered: Store full preset templates in workflow history. Rejected because large content must stay out of workflow histories and compact provenance is enough.
Test implications: Rerun contract, route, frontend, and integration tests.

## FR-005

Decision: Treat worker-facing no-live-catalog behavior as implemented and verified.
Evidence: `moonmind/workflows/temporal/worker_runtime.py` preserves expanded payload metadata; task contract tests reject unresolved preset include work; integration tests assert worker-facing payload contains resolved steps.
Rationale: Workers should consume the resolved execution payload, not authoring-time preset definitions.
Alternatives considered: Worker-side expansion at execution time. Rejected because it violates the MM-642 source invariant and makes already submitted work sensitive to live catalog changes.
Test implications: Rerun worker runtime/task contract tests and integration coverage.

## FR-007

Decision: Treat manual-only regression behavior as implemented and verified.
Evidence: API route and integration tests assert manual-only task submissions do not receive `authoredPresets` or `appliedStepTemplates`.
Rationale: Preset provenance must not be fabricated for tasks that do not use presets.
Alternatives considered: Always include empty preset arrays. Rejected because it changes submitted payload shape and weakens provenance semantics.
Test implications: Rerun route and integration tests.

## FR-008

Decision: Preserve `MM-642` and the canonical Jira preset brief in this artifact set and final verification.
Evidence: `spec.md` preserves the full orchestration input, and this plan references the Jira key explicitly.
Rationale: Final verification and PR metadata need a durable source trace independent of later Jira or catalog changes.
Alternatives considered: Reuse the related MM-630 artifacts. Rejected because they preserve a different Jira source key and cannot serve as MM-642 traceability evidence.
Test implications: Final MoonSpec verification must inspect artifacts for `MM-642`, DESIGN-REQ-010, and DESIGN-REQ-011.

## Success Criteria Coverage

Decision: Treat SC-001 through SC-007 as implemented and verified by the same focused evidence that verifies FR-001 through FR-008 and DESIGN-REQ-010 through DESIGN-REQ-011.
Evidence: `tests/unit/api/test_task_step_templates_service.py`, `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, `tests/unit/api/routers/test_executions.py`, `frontend/src/entrypoints/task-create.test.tsx`, `tests/integration/temporal/test_task_shaped_submission_normalization.py`, and the preserved MM-642 artifact set.
Rationale: The success criteria are measurable expressions of the same compile-time composition, provenance durability, worker independence, manual-only regression, and traceability behavior mapped in the functional requirements.
Alternatives considered: Create separate success-criteria-only tests. Rejected because existing unit and integration tests already exercise the observable outcomes directly.
Test implications: Rerun focused unit, frontend, and integration checks plus final MoonSpec verification when validating the story.

- SC-001 maps to FR-001 and FR-002 validation-before-finalization evidence.
- SC-002 maps to FR-003 deterministic order evidence.
- SC-003 maps to FR-004 provenance preservation evidence.
- SC-004 maps to FR-005 worker-facing payload independence evidence.
- SC-005 maps to FR-006 reconstruction metadata evidence.
- SC-006 maps to FR-007 manual-only regression evidence.
- SC-007 maps to FR-008 traceability preservation evidence.
