# Research: Submit Flattened Executable Steps with Provenance

## FR-001 / DESIGN-REQ-004 - Applied Preset Submission Shape

Decision: Implemented but MM-579-specific verification is missing.
Evidence: `frontend/src/entrypoints/task-create.tsx` applies preset previews into generated step state; `frontend/src/entrypoints/task-create.test.tsx` has MM-578 coverage for preview/apply and a submission assertion for a generated Tool step.
Rationale: Existing behavior likely submits applied generated Tool and Skill steps, but current focused submission evidence only asserts the generated Tool step. MM-579 requires proving the whole payload is flat executable Tool/Skill work.
Alternatives considered: Treat MM-578 evidence as sufficient. Rejected because MM-579 is specifically about flattened executable submission with provenance, so the submitted multi-step payload needs direct coverage.
Test implications: Frontend integration-boundary test first; implementation contingency in Create page serialization if the test fails.

## FR-002 / DESIGN-REQ-015 - Unresolved Preset Rejection

Decision: Implemented and verified.
Evidence: `moonmind/workflows/tasks/task_contract.py::TaskStepSpec._reject_forbidden_step_overrides`; `tests/unit/workflows/tasks/test_task_contract.py::test_task_steps_reject_non_executable_step_types`; `tests/unit/workflows/task_proposals/test_service.py::test_promote_proposal_rejects_unresolved_preset_steps`; MM-578 Create page unresolved Preset submission test.
Rationale: Runtime submission, proposal promotion, and UI submission boundaries all reject unresolved Preset steps.
Alternatives considered: Add a new linked-preset runtime mode. Rejected because MM-579 explicitly keeps runtime payloads flat by default and does not request linked-preset execution.
Test implications: No new implementation; rerun focused tests during final verification.

## FR-003 / DESIGN-REQ-006 - Complete Provenance Metadata

Decision: Partial; code preserves provenance metadata but canonical `presetVersion` coverage is missing.
Evidence: `TaskStepSource` currently models `kind`, `presetId`, `presetSlug`, `version`, `includePath`, and `originalStepId`; proposal tests preserve `includePath` and `originalStepId`; source design examples use `presetVersion`.
Rationale: MM-579 names `presetVersion` as required metadata for preset-derived source provenance when that provenance is produced by preset application or surfaced for review. The existing `version` field may represent the same concept, but the current code and tests do not prove the canonical source field survives. Because MoonMind is pre-release, the plan should update the canonical shape consistently rather than hide the mismatch, while preserving the separate rule that runtime execution cannot depend on provenance completeness.
Alternatives considered: Document `version` as equivalent to `presetVersion`. Rejected because the Jira preset brief and source design explicitly name `presetVersion`, and hidden transforms would weaken traceability.
Test implications: Unit tests for task contract and proposal promotion plus frontend submission tests for complete source metadata.

## FR-004 / DESIGN-REQ-016 - Audit, UI Grouping, Proposal Reconstruction, And Review

Decision: Partial; adjacent review surfaces exist, but MM-579 complete-metadata evidence is missing.
Evidence: `api_service/api/routers/task_proposals.py::_build_task_preview` summarizes preset provenance; `test_get_proposal_preview_includes_preset_provenance` covers preview metadata; Create page source label helpers display preset origin.
Rationale: Existing surfaces use provenance, but the tests do not yet assert complete MM-579 metadata across audit/review/reconstruction cases.
Alternatives considered: Add a new storage model for provenance. Rejected because existing task payload and proposal payload metadata should carry the required data.
Test implications: API unit and frontend integration tests; implementation contingency in preview/label mapping if fields are dropped.

## FR-005 / SC-003 - Runtime Independence From Provenance

Decision: Implemented and verified.
Evidence: `moonmind/workflows/temporal/worker_runtime.py` maps executable Tool and Skill steps by selected Step Type and selected Tool/Skill; `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` covers explicit Tool and Skill plan nodes with source metadata carried as inputs only.
Rationale: Runtime materialization does not require live preset lookup or provenance to choose execution behavior.
Alternatives considered: Resolve preset catalog during runtime planning. Rejected because source requirements say runtime correctness must not depend on live catalog lookup.
Test implications: Existing unit tests are sufficient; rerun during final verification.

## FR-006 - Deterministic Validated Preset Expansion

Decision: Implemented and verified by adjacent Create page behavior.
Evidence: MM-578 Create page tests cover preview before mutation, generated-step warnings, validation failure preserving the draft, apply replacement, and unresolved Preset blocking.
Rationale: Preset expansion is already an explicit preview/apply action with visible validation behavior before executable submission.
Alternatives considered: Expand automatically on selection. Rejected because it removes the required preview/validation decision point.
Test implications: Existing focused frontend tests plus MM-579 submission/provenance tests.

## FR-007 / FR-008 / DESIGN-REQ-023 - Promotable Proposal Flat Payload

Decision: Partial; promotion validates stored payloads, but explicit Tool/Skill flatness for preset-derived proposals needs stronger coverage.
Evidence: `TaskProposalService.promote_proposal` validates stored `CanonicalTaskPayload`; proposal service tests preserve provenance and reject unresolved Preset steps. Existing provenance-preservation proposal tests include source metadata but not explicit Tool/Skill step type.
Rationale: MM-579 requires stored promotable proposals to be executable by default. Accepting legacy source-only steps may be compatible with earlier behavior but does not prove flat Tool/Skill executable intent.
Alternatives considered: Defer proposal enforcement because runtime submission validation already exists. Rejected because MM-579 explicitly includes promotion semantics.
Test implications: Unit tests for proposal promotion requiring explicit Tool/Skill steps, no live catalog re-expansion, provenance preservation, and invalid stored Preset rejection.

## FR-009 / SC-005 - Explicit Refresh From Catalog

Decision: Implemented but MM-579-specific verification is missing for drafts; proposal refresh may remain out of scope if no explicit proposal refresh UI exists.
Evidence: Create page reapply-needed messaging and stale preview invalidation are covered by adjacent tests; source design requires explicit preview and validation before refresh.
Rationale: The authoring surface already treats reapply as explicit. MM-579 should add a focused regression to prevent automatic refresh from changing reviewed flat payloads.
Alternatives considered: Automatically refresh applied preset-derived steps on catalog updates. Rejected because it would drift from reviewed operator intent.
Test implications: Frontend integration test first; document any proposal-refresh limitation in final verification if no product surface exists.

## FR-010 / SC-006 - MM-579 Traceability

Decision: Partial until downstream artifacts and final verification exist.
Evidence: `spec.md` preserves MM-579 and the original Jira preset brief; `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` are generated in this step.
Rationale: Traceability must remain visible through tasks, implementation notes, verification, commit text, and PR metadata.
Alternatives considered: Rely only on Jira link metadata. Rejected because MoonSpec verification compares local artifacts to the original request.
Test implications: Artifact review in `/moonspec-verify`.
