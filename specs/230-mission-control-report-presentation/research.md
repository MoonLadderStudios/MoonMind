# Research: Surface Canonical Reports in Mission Control

## FR-001 / DESIGN-REQ-005 - Server-Driven Latest Report

Decision: Use the existing execution artifact endpoint with `link_type=report.primary&latest_only=true` to identify the canonical final report.
Evidence: `api_service/api/routers/temporal_artifacts.py` exposes `link_type` and `latest_only`; `moonmind/workflows/temporal/artifacts.py` routes latest lookup through `latest_for_execution_link`.
Rationale: This satisfies the source requirement that latest-report selection is server query behavior and avoids browser-side sorting over arbitrary artifacts.
Alternatives considered: Add a new report projection endpoint now. Rejected because `docs/Artifacts/ReportArtifacts.md` lists it as optional and existing APIs are sufficient.
Test implications: Frontend unit test for query URL plus API contract regression for `latest_only`.

## FR-002 / DESIGN-REQ-014 - Report-First Presentation

Decision: Add a report panel/top-level card in `frontend/src/entrypoints/task-detail.tsx` when the latest `report.primary` query returns an artifact.
Evidence: Current task detail renders Summary, Steps, Timeline, and Artifacts but no report panel.
Rationale: A dedicated report region makes the canonical report visible before generic artifact inspection.
Alternatives considered: Highlight report rows inside the existing artifact table. Rejected because the story requires report-first presentation before generic artifact inspection.
Test implications: Frontend unit test asserting Report appears before Artifacts.

## FR-003 / DESIGN-REQ-014 / DESIGN-REQ-016 - Related Report Content

Decision: Parse artifact links and metadata in the frontend schema, then derive related report content from existing artifact list rows with `report.summary`, `report.structured`, or `report.evidence` links.
Evidence: `ArtifactMetadataModel` includes `links`, `metadata`, `default_read_ref`, and `download_url`; current frontend schema only normalizes basic artifact fields.
Rationale: Related report content remains individually openable and uses the normal artifact list response as the read model.
Alternatives considered: Fetch each related link type separately. Rejected for the first runtime slice because the existing artifact list already carries link metadata and keeps the UI simple.
Test implications: Frontend unit test with summary/structured/evidence artifacts and open links.

## FR-004 - Preserve Generic Surfaces

Decision: Add the report panel without removing or hiding the existing artifact table, timeline, run summary, steps, stdout/stderr, diagnostics, or session continuity surfaces.
Evidence: `task-detail.tsx` already renders these sections independently from artifacts.
Rationale: Curated reports must complement observability, not replace it.
Alternatives considered: Move artifacts into the report panel. Rejected because generic artifacts must remain accessible for non-report deliverables and diagnostics.
Test implications: Existing task detail tests plus a report test that still finds the Artifacts section.

## FR-005 / DESIGN-REQ-015 - Viewer Target Selection

Decision: Add frontend helpers that choose report open targets from `default_read_ref` first, then explicit download URL, then artifact ID; label the viewer from `render_hint`, `content_type`, `metadata.name`, and `metadata.title`.
Evidence: API metadata includes `default_read_ref`; existing `artifactDownloadHref` uses only download URL or artifact ID.
Rationale: The UI can honor preview/raw read policy while keeping the first implementation as open/download navigation.
Alternatives considered: Build full inline renderers for every content type. Rejected because the source allows binary/download behavior and this story focuses on presentation and openability.
Test implications: Frontend unit tests for default-read-ref target and content-type labels.

## FR-006 / DESIGN-REQ-016 - Read Model Boundary

Decision: Treat report presentation as a read model over normal artifact metadata; do not add storage, mutation routes, or report-specific tables.
Evidence: `ArtifactMetadataModel` and execution artifact listing already provide the needed data.
Rationale: Keeps report UI aligned with existing artifact authorization, lifecycle, and preview behavior.
Alternatives considered: Add a separate report endpoint with custom persistence. Rejected as out of scope and contrary to the source design.
Test implications: Contract test validates the existing endpoint returns links/default read refs for report artifacts.

## FR-007 - No Local Report Fabrication

Decision: If the latest `report.primary` query returns no artifact, render no report panel and fall back to the existing artifact list.
Evidence: Current UI can render an empty artifact list state.
Rationale: The source forbids local report identity heuristics.
Alternatives considered: Guess from `metadata.report_type`, filenames, or content type. Rejected as explicitly forbidden.
Test implications: Frontend fallback test with generic artifacts and empty latest report response.

## FR-008 - MM-494 Traceability

Decision: Preserve MM-494 in the resumed MoonSpec artifacts and verification evidence while keeping the already-completed runtime story and implementation evidence under `specs/230-mission-control-report-presentation`.
Evidence: `spec.md`, `plan.md`, `tasks.md`, `verification.md`, and `spec.md` (Input) include MM-494.
Rationale: The current Jira orchestration input requires MM-494 traceability, and the existing feature directory already covers the same independently testable runtime behavior.
Alternatives considered: Open a duplicate feature directory for MM-494. Rejected because the story was already fully specified, implemented, and verified under the existing feature directory.
Test implications: Traceability grep in quickstart/final verification.
