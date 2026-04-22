# Research: Report Workflow Rollout and Examples

## FR-001 / DESIGN-REQ-003

Decision: Missing. Add deterministic report workflow example mappings for unit-test, coverage, pentest/security, and benchmark workflow families.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` has report link type and bundle helpers but no workflow family mappings.
Rationale: MM-464 requires runtime validation of examples, not only prose guidance.
Alternatives considered: Leave examples in `docs/Artifacts/ReportArtifacts.md`; rejected because runtime mode requires executable validation.
Test implications: Unit tests for each supported mapping.

## FR-002 / DESIGN-REQ-019

Decision: Missing. Include report link classes, observability link classes, and recommended metadata keys per workflow family.
Evidence: Metadata key validation exists globally, but no family-specific recommendation layer exists.
Rationale: The source examples define different metadata for unit-test, coverage, security, and benchmark reports.
Alternatives considered: Hardcode this guidance in producer workflows; rejected because it would duplicate rollout rules.
Test implications: Unit tests assert recommended metadata keys and artifact classes.

## FR-003 / FR-004 / DESIGN-REQ-007

Decision: Missing. Add validation that report-producing workflows include `report.primary`, keep evidence separate, and keep runtime stdout/stderr/diagnostics distinct from curated reports.
Evidence: Existing report bundle validation covers compact refs and final markers, but not workflow family rollout class validation.
Rationale: MM-464 specifically targets migration safety and examples for report-producing workflow families.
Alternatives considered: Depend solely on `publish_report_bundle`; rejected because rollout classification also needs to handle legacy generic-output executions.
Test implications: Unit tests for missing primary and separated artifact classes.

## FR-005 / FR-006 / DESIGN-REQ-020

Decision: Partial. Generic output links remain accepted by artifact service tests, but no report rollout classification helper identifies generic fallback.
Evidence: `tests/unit/workflows/temporal/test_artifacts.py` covers generic output links; no classification helper exists.
Rationale: Mission Control can fall back safely when runtime tells it there is no canonical report.
Alternatives considered: Let UI infer fallback by scanning artifacts; rejected because prior specs require server/query/projection behavior instead of local heuristics.
Test implications: Unit test classifies `output.primary`-only input as fallback.

## FR-007 / DESIGN-REQ-021

Decision: Missing. Add ordered rollout phase data for metadata conventions, report links/UI surfacing, compact bundle contract, and optional projections/filters/retention/pinning.
Evidence: Phases exist in `docs/Artifacts/ReportArtifacts.md` §19 only.
Rationale: Runtime helpers and tests can preserve migration sequencing without making docs the only source.
Alternatives considered: No runtime exposure; rejected because MM-464 asks to support incremental rollout phases.
Test implications: Unit test asserts ordered phase names.

## FR-008 / DESIGN-REQ-022

Decision: Partial. Existing compact bundle validation rejects unsafe bundle fields; add projection summary helper that only emits refs and bounded metadata.
Evidence: `build_report_bundle_result` and `validate_report_bundle_result` exist, but there is no convenience summary helper.
Rationale: Suggested fields/endpoints must remain read models over normal artifact refs.
Alternatives considered: Use raw bundle dictionaries directly in every consumer; rejected because unsafe projection inputs should fail fast.
Test implications: Unit tests for safe projection and unsafe inline body/raw URL rejection.
