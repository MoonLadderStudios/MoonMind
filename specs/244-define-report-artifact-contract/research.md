# Research: Report Artifact Contract

## FR-001 to FR-004 Core Report Link Semantics

Decision: implemented_verified; no new production code is planned unless later verification finds drift.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` defines `REPORT_ARTIFACT_LINK_TYPES`, `GENERIC_OUTPUT_LINK_TYPES`, `classify_report_rollout_artifacts`, and `validate_report_workflow_artifact_classes`; `tests/unit/workflows/temporal/test_artifacts.py` and `tests/unit/workflows/temporal/test_report_workflow_rollout.py` cover supported report links, fallback behavior, and generic-output separation.
Rationale: The runtime already treats report link semantics explicitly and preserves generic outputs as non-report outputs.
Alternatives considered: Re-derive report meaning from artifact names or render hints. Rejected because the current repo and source design both require server-defined report semantics instead of local heuristics.
Test implications: unit + contract/frontend verification only.

## FR-005 and FR-006 Compact Report Bundle Contract

Decision: implemented_verified; later work is verification-first.
Evidence: `build_report_bundle_result` and `validate_report_bundle_result` in `moonmind/workflows/temporal/report_artifacts.py`; `publish_report_bundle` in `moonmind/workflows/temporal/artifacts.py`; bundle validation and publication tests in `tests/unit/workflows/temporal/test_artifacts.py` plus activity delegation coverage in `tests/unit/workflows/temporal/test_artifacts_activities.py`.
Rationale: The repo already ships a compact `report_bundle_v = 1` result shape with explicit rejection of inline bodies, raw URLs, and oversized payloads.
Alternatives considered: Introduce a second report summary model or a looser untyped dict contract. Rejected because the existing bounded bundle model already matches the source design and is safer for workflow history.
Test implications: unit coverage is the primary proof; integration runner remains available if any bundle publication boundary changes later.

## FR-007 Report Metadata Validation

Decision: implemented_verified.
Evidence: `REPORT_METADATA_KEYS`, `_validate_report_metadata`, and `_validate_report_metadata_value` in `moonmind/workflows/temporal/report_artifacts.py`; unsafe metadata tests in `tests/unit/workflows/temporal/test_artifacts.py` cover unsupported keys, oversized values, secret-like keys, and secret-like values.
Rationale: Metadata validation already enforces a bounded, display-safe vocabulary aligned with the source design.
Alternatives considered: Lenient metadata passthrough with sanitization at presentation time only. Rejected because the source design requires safety at the contract boundary.
Test implications: unit only.

## FR-008 Canonical Report Resolution

Decision: implemented_verified.
Evidence: `TemporalArtifactService.list_for_execution(... link_type="report.primary", latest_only=True)` behavior is covered in `tests/unit/workflows/temporal/test_artifacts.py`; API query behavior is covered in `tests/contract/test_temporal_artifact_api.py`; Mission Control consumes server-selected `report.primary` artifacts in `frontend/src/entrypoints/task-detail.tsx` and corresponding tests.
Rationale: The current implementation already resolves canonical reports through explicit link type plus server-defined latest behavior.
Alternatives considered: Client-side artifact sorting or name-based selection. Rejected because the source design explicitly forbids heuristic-only report identification.
Test implications: contract + frontend unit.

## FR-009 Report, Evidence, and Observability Separation

Decision: implemented_verified.
Evidence: `ReportWorkflowMapping` separates `report_link_types` and `observability_link_types`; task detail fetches the canonical report separately from the generic artifact list and treats related report content distinctly.
Rationale: Existing code and tests already enforce the desired separation between curated report artifacts and observability artifacts.
Alternatives considered: Flatten report and observability content into one undifferentiated output class. Rejected because the source design treats them as related but distinct surfaces.
Test implications: unit + frontend unit.

## FR-010 Terminology and Contract-Facing Documentation

Decision: implemented_verified; preserve the terminology through final verification rather than planning new production changes.
Evidence: `docs/Artifacts/ReportArtifacts.md`, `moonmind/workflows/temporal/report_artifacts.py`, `specs/244-define-report-artifact-contract/data-model.md`, and `specs/244-define-report-artifact-contract/contracts/report-artifact-contract.md` all preserve the report artifact, report bundle, evidence artifact, final report, and intermediate report terminology.
Rationale: The runtime semantics and feature-local design artifacts now align, so no additional implementation is justified unless later verification finds a mismatch.
Alternatives considered: Treat terminology as still unverified until application code changes are made. Rejected because the existing canonical docs, runtime helper contract, and feature-local design artifacts already provide the required contract-facing terminology.
Test implications: none beyond final verify.

## FR-011 and SC-006 Traceability

Decision: partial; preserve MM-492 through downstream artifacts and final verification.
Evidence: `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-492-moonspec-orchestration-input.md` already preserve the Jira issue key and original brief.
Rationale: Traceability is partly satisfied but still needs plan/tasks/verification continuity.
Alternatives considered: Treat spec-only traceability as complete. Rejected because the story explicitly requires downstream preservation.
Test implications: traceability review during later tasks and final verification.

## Repo Gap Analysis Outcome

Decision: No clear production-code gap is currently required for MM-492; the story is primarily a contract-definition and verification story whose runtime behavior already exists from adjacent report-artifact work.
Evidence: Existing report artifact helpers, publication service methods, API contract tests, and Mission Control report presentation/tests all align with the MM-492 spec.
Rationale: Planning should not manufacture implementation work when the repo already satisfies most of the story. The correct next step is task generation that emphasizes verification first and implementation only if verification discovers drift.
Alternatives considered: Force new code changes to “touch” the feature anyway. Rejected because it would create unnecessary churn and violate the evidence-first planning model.
Test implications: Explicit verification-first tasks with unit, contract, and integration escalation paths.
