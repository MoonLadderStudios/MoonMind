# Research: Publish Report Bundles

## Story Classification

Decision: Treat MM-493 as a single-story runtime feature request and a verification-first planning story.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-493-moonspec-orchestration-input.md`; `specs/245-publish-report-bundles/spec.md`.
Rationale: The brief defines one independently testable runtime outcome: immutable report bundle publication through activity boundaries with compact workflow refs and server-driven final/latest report behavior.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one story and does not require processing multiple specs.
Test implications: Explicit unit, contract/frontend, and hermetic integration escalation paths are required.

## FR-001 / DESIGN-REQ-006 Activity-Owned Publication

Decision: implemented_verified.
Evidence: `moonmind/workflows/temporal/artifacts.py` defines `publish_report_bundle`; `tests/unit/workflows/temporal/test_artifacts_activities.py` verifies the activity facade delegates bundle publication; `tests/unit/workflows/temporal/test_artifacts.py` exercises the helper directly.
Rationale: The current runtime already publishes report bundles through an activity/service boundary rather than workflow code.
Alternatives considered: Treat the helper as unverified until a new integration test is added. Rejected because focused unit evidence already covers the service and activity boundary.
Test implications: Focused unit rerun; integration only if later fixes touch the boundary.

## FR-002 / DESIGN-REQ-005 Compact Workflow-Safe Bundle State

Decision: implemented_verified.
Evidence: `build_report_bundle_result` and `validate_report_bundle_result` in `moonmind/workflows/temporal/report_artifacts.py`; unsafe bundle tests in `tests/unit/workflows/temporal/test_artifacts.py`; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` verifies unsafe bundle rejection.
Rationale: The repo already enforces `report_bundle_v = 1` and rejects inline report bodies, logs, screenshots, transcripts, raw URLs, and oversized payloads.
Alternatives considered: Add a second serialization contract for MM-493. Rejected because the existing bounded bundle contract already matches the source design.
Test implications: Focused unit verification only.

## FR-003 / FR-004 / DESIGN-REQ-012 / DESIGN-REQ-013 Execution And Step Linkage

Decision: implemented_verified.
Evidence: `publish_report_bundle` copies execution metadata plus optional `step_id` and `attempt`; `tests/unit/workflows/temporal/test_artifacts.py` asserts bundle refs, `report.primary` linkage, `report_scope`, `is_final_report`, `step_id`, and `attempt`; contract tests cover latest execution-scoped report lookup.
Rationale: Execution identity and bounded step metadata are already part of the artifact publication path and remain artifact-backed rather than workflow-history payloads.
Alternatives considered: Treat step-aware coverage as partial until a dedicated MM-493 feature-local test exists. Rejected because the current tests already verify the concrete runtime fields.
Test implications: Focused unit plus contract reruns.

## FR-005 / DESIGN-REQ-020 Canonical Final Report Marker

Decision: implemented_verified.
Evidence: `publish_report_bundle` enforces that final bundles include exactly one final report; unit tests in `tests/unit/workflows/temporal/test_artifacts.py` and `tests/unit/workflows/temporal/test_report_workflow_rollout.py` cover missing and duplicate final-marker failures.
Rationale: The canonical final-report invariant already exists in runtime code and verification.
Alternatives considered: Re-implement final-report validation in a new helper. Rejected because the existing helper already enforces the invariant.
Test implications: Focused unit rerun only.

## FR-006 / DESIGN-REQ-021 Server-Defined Latest Report Resolution

Decision: implemented_verified.
Evidence: `TemporalArtifactService.list_for_execution(... latest_only=True)` supports latest `report.primary` selection in `moonmind/workflows/temporal/artifacts.py`; contract coverage in `tests/contract/test_temporal_artifact_api.py`; Mission Control fetch and render path in `frontend/src/entrypoints/task-detail.tsx` with matching tests in `frontend/src/entrypoints/task-detail.test.tsx`.
Rationale: Canonical latest-report resolution is already link-driven and server-defined; the UI consumes the selected artifact instead of sorting artifacts locally.
Alternatives considered: Add a new UI-specific latest-report resolver. Rejected because the current API/UI path already meets the story.
Test implications: Contract + frontend unit verification.

## FR-007 / DESIGN-REQ-019 Multi-Workflow-Family Publication

Decision: implemented_verified.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` includes rollout validation and report-workflow mapping logic; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` covers report-producing workflow families and unsafe bundle behavior.
Rationale: The current runtime already supports multiple report-producing workflow families under one artifact-backed contract without forcing a single findings schema.
Alternatives considered: Treat this as partial until more workflow families are added. Rejected because MM-493 requires the shared contract, not additional families.
Test implications: Focused unit rerun only.

## FR-008 Immutable Coexistence Of Intermediate And Final Reports

Decision: implemented_verified.
Evidence: `test_latest_report_primary_coexists_with_intermediate_report_without_mutation` in `tests/unit/workflows/temporal/test_artifacts.py` proves a later intermediate report produces a new artifact ID, leaves the prior final report artifact readable and marked final, and lets latest `report.primary` resolution move forward without mutating the earlier artifact.
Rationale: The story-specific coexistence and non-mutation evidence is now explicit in the unit suite rather than inferred indirectly from artifact immutability alone.
Alternatives considered: Keep the item as unverified until a separate contract or UI test was added. Rejected because the new unit test directly exercises the runtime publication and retrieval path that the requirement depends on.
Test implications: Focused unit rerun plus the existing contract/frontend verification remain sufficient.

## FR-009 Traceability

Decision: partial.
Evidence: `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-493-moonspec-orchestration-input.md` preserve MM-493 and the original brief.
Rationale: Downstream plan, tasks, and verification artifacts still need to preserve the Jira issue key for final verification.
Alternatives considered: Treat spec-only preservation as sufficient. Rejected because the story explicitly requires downstream traceability.
Test implications: Final traceability review in later tasks and verification.

## Repo Gap Analysis Outcome

Decision: No production-code change is clearly required at planning time; MM-493 should proceed as verification-first.
Evidence: Existing runtime helpers, unit tests, contract tests, and frontend tests already cover activity-side publication, compact bundle validation, final marker enforcement, and latest `report.primary` consumption.
Rationale: Planning should not invent implementation work when the runtime already appears aligned with the story.
Alternatives considered: Force additional code changes to create visible implementation churn. Rejected because that would weaken the evidence-first planning discipline.
Test implications: Tasks should prioritize focused unit + contract/frontend verification, full unit rerun, and hermetic integration escalation only if fixes become necessary.
