# Research: Report Artifact Contract

## FR-001 / DESIGN-REQ-001 Existing Artifact Storage

Decision: Implement report publishing as validation around the existing artifact create/link paths.
Evidence: `moonmind/workflows/temporal/artifacts.py` already creates artifacts, persists metadata, links execution refs, and lists artifacts by execution.
Rationale: MM-460 explicitly requires reports to remain artifact families in the existing system.
Alternatives considered: A report-specific table or store was rejected by the source brief and Constitution storage constraints.
Test implications: Service integration-style tests should create and link report artifacts through `TemporalArtifactService`.

## FR-002 / FR-003 Report Link Types

Decision: Define the supported report link types as runtime constants and reject unsupported `report.*` values.
Evidence: Current `ExecutionRef.link_type` is a free-form string, so `report.unknown` would currently be accepted.
Rationale: Stable report semantics require a bounded vocabulary while allowing existing non-report types.
Alternatives considered: Database enum migration was rejected because arbitrary non-report link types remain valid and no new storage is planned.
Test implications: Unit tests cover supported/unsupported link types; service tests cover create/link failure paths.

## FR-004 / FR-005 Bounded Metadata

Decision: Validate report metadata with an allowlist, compact value limits, and secret-like key/value rejection.
Evidence: `TemporalArtifactService.create()` currently stores `metadata_json` directly after image attachment reserved-key checks.
Rationale: Control-plane metadata must be safe and bounded; detailed findings belong in artifacts.
Alternatives considered: Redacting metadata in place was rejected for this story because fail-closed behavior is clearer and safer at the artifact boundary.
Test implications: Unit tests cover allowed metadata, unknown keys, large strings, nested payloads, and secret-like keys/values.

## FR-006 Generic Outputs

Decision: Keep `output.primary`, `output.summary`, and `output.agent_result` outside report validation.
Evidence: Existing workload code declares `output.primary` and `output.summary`; artifact service tests already create `output.primary` links.
Rationale: Source requirements explicitly say generic output flows continue to work for non-report deliverables.
Alternatives considered: Auto-reclassifying generic outputs as reports was rejected by source non-goals.
Test implications: Add a regression that generic output metadata can still contain non-report keys.

## FR-008 Latest Report Discovery

Decision: Use existing latest-by-execution-link behavior for `report.primary`.
Evidence: `TemporalArtifactRepository.latest_for_execution_link()` filters by namespace, workflow ID, run ID, and link type.
Rationale: The source doc defines latest-report selection as server query behavior, not mutable state.
Alternatives considered: A separate latest-report pointer was rejected because it adds storage and mutable state.
Test implications: Create two `report.primary` artifacts and verify `latest_only=True` returns the newer artifact.

## Test Tooling

Decision: Use `tests/unit/workflows/temporal/test_artifacts.py` for artifact-service integration-style coverage and focused pure-unit tests for the new validator.
Evidence: The test file already provides isolated async SQLite fixtures and local artifact store coverage.
Rationale: This validates the real service boundary without Docker or external credentials.
Alternatives considered: Compose-backed integration tests are useful but unnecessary for this narrow service contract.
Test implications: Targeted command is `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_artifacts.py`.
