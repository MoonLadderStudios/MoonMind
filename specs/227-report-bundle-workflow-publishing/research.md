# Research: Report Bundle Workflow Publishing

## Story Classification

Decision: Treat MM-461 as a single-story runtime feature request.
Evidence: `spec.md` (Input); `specs/227-report-bundle-workflow-publishing/spec.md`.
Rationale: The brief names one actor, one activity-side publication outcome, one compact bundle shape, and one bounded acceptance set.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one independently testable story.
Test implications: Unit and activity-boundary integration tests are required.

## FR-001 / DESIGN-REQ-018

Decision: Missing. Add an activity-facing report bundle publication path.
Evidence: `moonmind/workflows/temporal/artifacts.py` has generic `create`, `write_complete`, and link methods; no report bundle helper or activity facade method exists.
Rationale: Individual report artifacts can be created after MM-460, but workflows still need a safe activity-owned path that assembles components and returns compact refs.
Alternatives considered: Let each workflow hand-roll create/write/link calls; rejected because it repeats safety-sensitive validation and final-report invariants.
Test implications: Unit coverage for service helper and activity-boundary coverage for facade invocation.

## FR-002 / FR-007 / DESIGN-REQ-008

Decision: Partial. Add bundle-level validation that rejects embedded bodies, evidence blobs, logs, screenshots, raw URLs, transcripts, and large finding details in workflow-facing payloads.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` validates report artifact link types and metadata, but not compact bundle return values.
Rationale: MM-460 protects artifact metadata; MM-461 protects workflow history and activity return shape.
Alternatives considered: Rely on code review conventions; rejected because workflow-history bloat and secret-bearing URLs must fail fast.
Test implications: Unit tests for unsafe key/value rejection and large inline payload rejection.

## FR-003 / DESIGN-REQ-014

Decision: Implemented unverified for bundle flow. Existing artifact links store namespace, workflow_id, run_id, link_type, and label.
Evidence: `TemporalArtifactRepository.add_link` and `TemporalArtifactService.create` in `moonmind/workflows/temporal/artifacts.py`.
Rationale: The underlying model exists, but MM-461 requires proof that the bundle helper uses it for every component.
Alternatives considered: No new tests; rejected because the story is specifically about the activity-side bundle path.
Test implications: Unit test should inspect links for primary, summary, structured, and evidence artifacts.

## FR-004 / DESIGN-REQ-017

Decision: Partial. Add explicit step metadata support to report bundle publication.
Evidence: `REPORT_METADATA_KEYS` allows `step_id` and `attempt`, but no bundle helper applies them consistently.
Rationale: Step-aware metadata must be bounded and producer-controlled; UI clients should not infer report identity from local heuristics.
Alternatives considered: Require callers to duplicate step metadata on every component; rejected because it is easy to omit or drift.
Test implications: Unit test should verify step_id, attempt, and scope are attached when supplied.

## FR-005

Decision: Missing. Add exactly-one final report validation.
Evidence: Existing report artifact validation allows `is_final_report` and `report_scope`, but does not enforce final bundle cardinality.
Rationale: Final reports need one canonical read target.
Alternatives considered: Let latest `report.primary` query decide; rejected because final bundle payload should be internally coherent.
Test implications: Unit tests for missing final marker and duplicate final marker failures.

## FR-006 / DESIGN-REQ-006 / DESIGN-REQ-010

Decision: Missing. Add `report_bundle_v = 1` result model with artifact refs and bounded metadata.
Evidence: `rg -n "report_bundle_v|ReportBundle"` only finds specs and docs, not runtime code.
Rationale: A stable compact result shape lets workflows carry refs without carrying report content.
Alternatives considered: Return ad hoc dictionaries; rejected because validation and tests need a stable contract.
Test implications: Unit tests for serialized shape.

## FR-008

Decision: Missing. Add evidence refs as a first-class tuple/list in the report bundle result.
Evidence: `report.evidence` link type exists, but no bundle result aggregates evidence refs.
Rationale: Evidence should remain separately addressable from the rendered report.
Alternatives considered: Store evidence only inside structured report JSON; rejected by the source brief.
Test implications: Unit test should publish multiple evidence artifacts and assert separate refs are returned.

## FR-009

Decision: Implemented unverified. Preserve MM-461 in all artifacts and final evidence.
Evidence: `spec.md`, `plan.md`, and orchestration input include MM-461.
Rationale: Traceability is required for Jira and PR handoff.
Alternatives considered: None.
Test implications: Final `rg` traceability check.
