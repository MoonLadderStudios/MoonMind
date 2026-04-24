# Research: Apply Report Access and Lifecycle Policy

## FR-001/FR-002/FR-004/DESIGN-REQ-017 - Existing Authorization, Preview, And Raw Access Boundary

Decision: Treat report artifacts as ordinary temporal artifacts for read and raw-download authorization and rely on preview/default-read behavior when raw access is restricted.
Evidence: `TemporalArtifactService._assert_read_access`, `_assert_raw_access`, `get_read_policy`, `read`, and `presign_download` already apply to report artifacts.
Rationale: MM-495 requires report preview behavior to stay inside the existing artifact authorization model instead of widening raw access.
Alternatives considered: Add report-specific access rules. Rejected because that would duplicate the artifact contract and widen scope.
Test implications: Unit tests should exercise restricted report artifacts through the existing service boundary.

## FR-003/DESIGN-REQ-011 - Bounded And Safe Report Metadata

Decision: Enforce report metadata safety through the existing report artifact contract validator.
Evidence: `validate_report_artifact_contract` rejects unsupported keys, secret-like values, cookies/session-token style values, and oversized nested payloads before report link publication.
Rationale: MM-495 explicitly requires report metadata to stay bounded and safe for control-plane display.
Alternatives considered: Add a separate metadata sanitizer at read time. Rejected because invalid metadata should fail at publication time, not after persistence.
Test implications: Unit tests should prove safe bounded metadata is accepted and unsafe or oversized metadata is rejected.

## FR-005/FR-006/DESIGN-REQ-018 - Report-Aware Retention Defaults

Decision: Derive `long` retention for `report.primary` and `report.summary`; keep `report.structured` and `report.evidence` at `standard` unless explicitly overridden.
Evidence: `_derive_retention` maps primary and summary reports to `long`, while structured/evidence remain non-observability retention with explicit override support.
Rationale: MM-495 requires report-specific retention recommendations without introducing a separate lifecycle model.
Alternatives considered: Require producers to pass explicit retention for all report artifacts. Rejected because the story requires default mappings.
Test implications: Unit tests should cover report default retention and explicit `long` overrides.

## FR-007/DESIGN-REQ-018 - Unpin Restoration

Decision: After unpinning a report artifact, restore retention from existing artifact links rather than falling back to generic `standard`.
Evidence: `TemporalArtifactService.unpin` recomputes retention based on report links.
Rationale: A pinned final report should return to report-derived retention after unpin.
Alternatives considered: Store previous retention in the pin record. Rejected because existing link type data is sufficient and avoids schema changes.
Test implications: Unit tests should cover pin/unpin on `report.primary`.

## FR-008/FR-009/DESIGN-REQ-018 - Artifact-Native Deletion

Decision: Preserve existing soft/hard delete behavior and rely on report-specific lifecycle regression coverage to prove no cascade into observability artifacts.
Evidence: `soft_delete` and `hard_delete` operate on one artifact ID; integration coverage verifies unrelated `runtime.stdout` remains intact.
Rationale: MM-495 requires deletion to stay artifact-native and not implicitly delete unrelated observability artifacts.
Alternatives considered: Add report-specific deletion code. Rejected because deletion must remain inside the existing artifact lifecycle path.
Test implications: Integration coverage should create one report and one runtime observability artifact on the same execution, delete the report, and verify observability remains readable.
