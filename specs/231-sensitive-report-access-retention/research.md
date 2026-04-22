# Research: Sensitive Report Access and Retention

## FR-001/FR-003/DESIGN-REQ-015 - Existing Authorization Boundary

Decision: Treat report artifacts as ordinary temporal artifacts for read and raw-download authorization.
Evidence: `TemporalArtifactService._assert_read_access`, `_assert_raw_access`, `read`, and `presign_download` already apply to every artifact, including report links.
Rationale: The source design explicitly says reports use the existing artifact authorization model rather than a report-specific one.
Alternatives considered: Add report-specific access rules. Rejected because it would widen scope and risk conflicting with the artifact contract.
Test implications: Unit tests should exercise restricted report artifacts through the existing service.

## FR-002/DESIGN-REQ-015 - Preview And Default Read

Decision: Verify existing `get_read_policy` behavior for restricted report artifacts with generated previews.
Evidence: `write_complete` calls `_create_preview_if_required`; `get_read_policy` returns preview `default_read_ref` when raw access is not allowed.
Rationale: This matches the report design without new API shape.
Alternatives considered: Store a report-specific default read field. Rejected because `default_read_ref` already exists.
Test implications: Unit test metadata/default-read policy for a restricted `report.primary`.

## FR-004/FR-005/DESIGN-REQ-016 - Primary And Summary Retention

Decision: Derive `long` retention for `report.primary` and `report.summary` when retention is not explicitly provided.
Evidence: `_derive_retention` currently maps only generic output/debug/input link types and otherwise falls back to `standard`.
Rationale: MM-463 and `docs/Artifacts/ReportArtifacts.md` §15.1 require primary and summary reports to default to long retention.
Alternatives considered: Require producers to pass explicit retention. Rejected because the story requires default mappings.
Test implications: Unit tests should fail first for current `standard` behavior, then pass after retention mapping is added.

## FR-006/DESIGN-REQ-016 - Structured And Evidence Retention

Decision: Keep default `standard` retention for `report.structured` and `report.evidence`, while allowing explicit `long` overrides.
Evidence: The source brief allows standard or long based on report family and audit needs.
Rationale: `standard` is the least surprising safe default when no product policy or explicit retention is provided, and it stays distinct from observability retention such as logs/diagnostics.
Alternatives considered: Make all report artifacts `long`. Rejected because the source explicitly permits standard for structured/evidence.
Test implications: Unit tests should verify structured/evidence defaults are `standard` and explicit `long` remains honored.

## FR-007/DESIGN-REQ-016 - Unpin Restoration

Decision: After unpinning a report artifact, restore retention from existing artifact links rather than always falling back to generic `standard`.
Evidence: `TemporalArtifactService.unpin` currently resets pinned artifacts to `standard`.
Rationale: A pinned `report.primary` should return to report-derived `long` retention after unpin.
Alternatives considered: Store previous retention in the pin record. Rejected because the existing link type is enough to recompute the default and avoids schema changes.
Test implications: Unit test pin/unpin on `report.primary`.

## FR-008/FR-009/DESIGN-REQ-022 - Artifact-Native Deletion

Decision: Preserve existing soft/hard delete behavior and add a report-specific no-cascade regression.
Evidence: `soft_delete` and `hard_delete` operate on one artifact ID; existing lifecycle tests cover generic deletion and pinned skip.
Rationale: The story requires confidence that report deletion does not delete unrelated observability artifacts.
Alternatives considered: Add report-specific deletion code. Rejected because deletion must remain artifact-system-native.
Test implications: Integration test creates one report and one runtime observability artifact on the same execution, deletes the report, and verifies observability remains complete/readable.
