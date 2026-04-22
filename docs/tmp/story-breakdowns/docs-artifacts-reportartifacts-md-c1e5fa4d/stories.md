# Story Breakdown: Report Artifacts

- Source design: `docs/Artifacts/ReportArtifacts.md`
- Source reference path for every story: `docs/Artifacts/ReportArtifacts.md`
- Story extraction date: `2026-04-22T07:25:46Z`
- Requested output mode: `jira`

## Design Summary

Report-producing workflows should model reports as first-class, immutable artifacts on top of the existing MoonMind artifact system. A report is normally a bundle of linked artifacts: a canonical human-facing report, optional summary, structured results, and separately addressable evidence. Workflows and activities must keep large report bodies, evidence, logs, and diagnostics artifact-backed while returning compact refs and bounded metadata. Mission Control should surface canonical final reports directly, keep observability and evidence distinct, and apply existing artifact authorization, preview, retention, deletion, and pinning behavior. The design explicitly avoids a separate report store, PDF/rendering mandates, provider-specific prompting, full-text indexing, legal review procedures, and treating every generic output as a report.

## Coverage Points

- `DESIGN-REQ-001` (requirement, 1. Purpose; 4. Goals): Report workflows produce first-class final deliverables - MoonMind must represent report-producing workflows as clear, queryable end states for unit-test, coverage, benchmark, compliance, security, incident, and technical-writeup reports.
- `DESIGN-REQ-002` (architecture, 1.1 Related docs and ownership boundaries): Report-specific ownership boundary - The report artifact document owns report-specific artifact contracts and presentation, while broader artifact identity, storage, live logs, and runtime result contracts remain owned by existing canonical docs.
- `DESIGN-REQ-003` (non-goal, 2. Scope / Non-goals; 5. Non-goals): Explicit scope and non-goals - The work covers report classes, bundles, metadata, separation from evidence/logs, retention/access expectations, and examples, while excluding PDF engines, provider prompts, full-text indexing, legal review, separate storage, mutable updates, and treating every output as a report.
- `DESIGN-REQ-004` (constraint, 3. Core decision; 11. Storage and linkage rules; 21. Bottom line): No separate report storage system - Reports must use the existing artifact store, artifact IDs, lifecycle, execution links, preview, and raw-download behavior rather than a new report blob store.
- `DESIGN-REQ-005` (artifact, 8. Recommended report artifact classes): Stable report link types - Report workflows should use explicit report.* link types including report.primary, report.summary, report.structured, report.evidence, and optional appendix, findings_index, and export classes.
- `DESIGN-REQ-006` (state-model, 6.2 Report bundle; 9. Report bundle model): Report bundles preserve distinct artifacts - A report deliverable is usually a bundle containing human-facing report content, summaries, structured results, and evidence as separately linked artifact refs plus bounded metadata.
- `DESIGN-REQ-007` (constraint, 3. Core decision; 7. Consumer and producer invariants; 13. Relationship to observability and diagnostics): Curated reports stay separate from observability - stdout, stderr, merged logs, diagnostics, provider snapshots, and session continuity artifacts remain operational truth surfaces and must not be collapsed into the report by default.
- `DESIGN-REQ-008` (constraint, 3. Core decision; 9. Report bundle model; 16. Workflow integration guidance): Workflow history remains compact - Workflows and activities must pass artifact refs and bounded summaries, not large report bodies, screenshots, evidence blobs, raw logs, or raw download URLs in workflow history.
- `DESIGN-REQ-009` (artifact, 10. Metadata model for report artifacts): Bounded report metadata model - Report metadata should standardize bounded keys such as artifact_type, report_type, scope, title, producer, subject, render_hint, final-report flag, counts, step_id, and attempt, while excluding secrets and large inline payloads.
- `DESIGN-REQ-010` (integration, 11.2 Execution linkage; 11.3 Step-aware linkage): Execution and step-aware linkage - Report artifacts should link to producing executions using namespace, workflow_id, run_id, link_type, labels, and optional bounded step metadata.
- `DESIGN-REQ-011` (integration, 11.4 Latest report semantics; 12.4 Report-first UX rule): Latest report is server query behavior - Latest/canonical report selection should be resolved by server-side query or projection semantics rather than browser-side sorting or local heuristics.
- `DESIGN-REQ-012` (requirement, 12. Presentation rules): Mission Control report-first presentation - Report-producing executions should expose a report panel or top-level report card, related evidence, and continued access to generic artifacts and observability surfaces.
- `DESIGN-REQ-013` (integration, 12.2 Default read behavior; 12.3 Recommended renderer behavior): Viewer selection uses artifact presentation contract - Primary report rendering should use default_read_ref, render_hint, content_type, metadata names/titles, and existing viewer behaviors for markdown, JSON, plain text, diff, image, PDF, and binary artifacts.
- `DESIGN-REQ-014` (artifact, 7. Consumer and producer invariants; 12.5 Evidence presentation): Evidence remains individually addressable - Evidence artifacts such as screenshots, transcripts, structured findings, command results, and excerpts should remain durable, separately addressable, and viewable where safe.
- `DESIGN-REQ-015` (security, 7. Consumer and producer invariants; 14. Security and access model): Sensitive report access degrades safely - Reports may contain sensitive details, findings, credentials, PII, or provider payloads; raw access must respect existing artifact authorization and may use previews via default_read_ref.
- `DESIGN-REQ-016` (requirement, 15. Retention guidance): Report retention and pinning behavior - Primary reports and summaries should prefer long retention, structured/evidence retention should follow policy, final reports should be pinnable, and deletion should remain artifact-system-native without implicit unrelated observability deletion.
- `DESIGN-REQ-017` (architecture, 16. Workflow integration guidance): Activity-owned report publication - Activities should assemble report content, write report artifacts, link them to execution and step metadata, and return compact report bundles to workflow code.
- `DESIGN-REQ-018` (state-model, 16.3 Finalization rule): Canonical final report marking - When a report is the primary deliverable, the producing path should clearly mark one artifact using report.primary, metadata.is_final_report=true, and metadata.report_scope=final.
- `DESIGN-REQ-019` (requirement, 17. Example workflow mappings): Workflow family mappings - Unit test, coverage, pentest/security, and benchmark workflows should fit the same report artifact family using appropriate report bundle pieces and metadata conventions.
- `DESIGN-REQ-020` (integration, 18. Suggested API/UI extensions): Report-aware API conveniences are projections - Convenience execution fields and an optional report projection endpoint may be added, but they must remain projections over normal artifacts rather than a second storage model.
- `DESIGN-REQ-021` (migration, 19. Migration guidance): Incremental migration without flag-day cutover - Rollout should allow generic outputs to continue, add explicit report.* semantics for new workflows, and let the UI degrade gracefully when only generic output artifacts exist.
- `DESIGN-REQ-022` (constraint, 20. Open questions): Open product policy decisions remain bounded - Unresolved choices include report_type enum strategy, auto-pinning defaults, projection timing, export semantics, evidence grouping, and multi-step task report projections.

## Ordered Story Candidates

### STORY-001: Report Artifact Contract

- Short name: `report-artifact-contract`
- Source reference: `docs/Artifacts/ReportArtifacts.md`
- Source sections: 1. Purpose, 1.1 Related docs and ownership boundaries, 2. Scope / Non-goals, 3. Core decision, 5. Non-goals, 8. Recommended report artifact classes, 10. Metadata model for report artifacts, 11. Storage and linkage rules, 21. Bottom line
- Description: As a workflow producer, I can publish report deliverables using explicit report artifact link types and bounded metadata in the existing artifact system, so reports become first-class without creating a separate storage plane.
- Independent test: Create representative report artifacts through the artifact API/service and assert report.* link types plus bounded metadata are persisted, indexed, and exposed without introducing report-specific storage or accepting large/secret metadata values.
- Dependencies: None
- Needs clarification: None
- Acceptance criteria:
  - Given a report-producing workflow publishes a canonical report, when the artifact is linked, then it uses link_type = report.primary and remains stored in the existing artifact store.
  - Given summary, structured results, evidence, appendix, findings-index, or export artifacts are part of a report deliverable, then they use the corresponding report.* link type instead of generic output classes.
  - Given report metadata is stored, then only bounded display and classification fields such as artifact_type, report_type, report_scope, title, producer, subject, render_hint, counts, step_id, and attempt are accepted for control-plane use.
  - Given metadata contains secrets, raw access grants, cookies, session tokens, or large inline payloads, then publication rejects or redacts those fields according to the existing artifact boundary.
  - Generic output.primary, output.summary, and output.agent_result flows continue to work for non-report deliverables.
- Requirements:
  - Define stable report link types for report.primary, report.summary, report.structured, report.evidence, report.appendix, report.findings_index, and report.export.
  - Represent reports as artifact families in the existing artifact system, not as a new storage system.
  - Standardize bounded report metadata keys while keeping detailed findings and large content in artifacts.
  - Keep PDF conversion, provider-specific prompting, full-text indexing, legal review, mutable report updates, and treating every generic output as a report out of scope.
- Owned coverage:
  - `DESIGN-REQ-001`: Provides first-class report deliverables through explicit artifact classes.
  - `DESIGN-REQ-002`: Keeps this story scoped to report-specific artifact and presentation contracts while relying on existing artifact ownership boundaries.
  - `DESIGN-REQ-003`: Owns the in-scope report classes and metadata while preserving explicit non-goals.
  - `DESIGN-REQ-004`: Requires use of the existing artifact system and rejects separate report storage.
  - `DESIGN-REQ-005`: Defines the stable report.* link types.
  - `DESIGN-REQ-009`: Owns bounded report metadata rules and exclusions.
- Assumptions:
  - Existing artifact link creation and metadata validation paths can be extended without schema-breaking storage changes.

### STORY-002: Report Bundle Workflow Publishing

- Short name: `report-bundle-publishing`
- Source reference: `docs/Artifacts/ReportArtifacts.md`
- Source sections: 3. Core decision, 6. Definitions, 7. Consumer and producer invariants, 9. Report bundle model, 11.2 Execution linkage, 11.3 Step-aware linkage, 16. Workflow integration guidance, 16.3 Finalization rule
- Description: As a workflow author, I can publish a report bundle from activities and return compact refs to workflow code, so report bodies and evidence remain durable without bloating workflow history.
- Independent test: Run a representative report-producing workflow/activity boundary test and assert the workflow result contains only artifact refs and bounded metadata while linked artifacts contain the primary report, structured output, and evidence.
- Dependencies: STORY-001
- Needs clarification: None
- Acceptance criteria:
  - Given an activity creates a report bundle, then it writes each report component as an artifact and links it to namespace, workflow_id, run_id, link_type, and optional label.
  - Given a report is step-scoped or iterative, then bounded step metadata such as step_id, attempt, and scope is attached without embedding report content in workflow history.
  - Given a report is the final deliverable, then exactly one canonical final report is identifiable via report.primary, metadata.is_final_report = true, and metadata.report_scope = final.
  - Given evidence such as screenshots, command results, transcripts, excerpts, or structured findings exists, then it remains separately addressable instead of being buried only inside a rendered report.
  - Workflow return values and persisted workflow state contain artifact_ref_v/artifact_id refs and bounded counts, not report bodies, evidence blobs, logs, screenshots, or raw download URLs.
- Requirements:
  - Standardize a compact report_bundle_v = 1 result shape with refs for primary_report_ref, summary_ref, structured_ref, evidence_refs, report_type, report_scope, sensitivity, and bounded counts.
  - Keep report body, finding details, screenshots, logs, transcripts, and evidence artifact-backed.
  - Make activities responsible for assembling report content, writing artifacts, linking artifacts, and returning compact bundles.
  - Support execution-level and step-aware report linkage using existing artifact link semantics.
- Owned coverage:
  - `DESIGN-REQ-006`: Owns the report bundle model as distinct linked artifacts.
  - `DESIGN-REQ-008`: Ensures workflows carry compact refs and bounded summaries only.
  - `DESIGN-REQ-010`: Owns execution and step-aware linkage behavior.
  - `DESIGN-REQ-014`: Keeps evidence artifacts individually addressable.
  - `DESIGN-REQ-017`: Places report assembly and publication at the activity boundary.
  - `DESIGN-REQ-018`: Defines canonical final-report marking in the producing path.
- Assumptions:
  - A representative workflow/activity test can exercise artifact creation without requiring live external providers.

### STORY-003: Mission Control Report Presentation

- Short name: `report-presentation`
- Source reference: `docs/Artifacts/ReportArtifacts.md`
- Source sections: 11.4 Latest report semantics, 12. Presentation rules, 12.1 Primary UI surfaces, 12.2 Default read behavior, 12.3 Recommended renderer behavior, 12.4 Report-first UX rule, 12.5 Evidence presentation, 18. Suggested API/UI extensions
- Description: As an operator, I can open an execution with a final report and see the canonical report, related evidence, and normal observability surfaces without guessing which generic artifact matters.
- Independent test: Seed an execution detail response with report.primary plus related report artifacts and verify Mission Control renders a report-first surface using server-provided latest-report/projection data, with artifact and log panels still available.
- Dependencies: STORY-001, STORY-002
- Needs clarification: Should the first implementation add a dedicated report projection endpoint immediately, or expose only execution detail summary fields?; Should multi-step tasks expose both per-step reports and one task-level final report projection in Mission Control?
- Acceptance criteria:
  - Given an execution has a canonical report.primary artifact, then Mission Control shows a report panel or top-level report card before requiring inspection of the generic artifact list.
  - Given linked report.summary, report.structured, or report.evidence artifacts exist, then they are shown as related report content and remain individually openable where access permits.
  - Given no report.primary artifact exists, then the UI falls back to the normal artifact list without fabricating report status from local heuristics.
  - Viewer selection uses default_read_ref, render_hint, content_type, metadata.name, and metadata.title, including appropriate markdown, JSON, text, diff, image, PDF, and binary handling.
  - Latest report selection comes from server query behavior or a projection field, not browser-side sorting of arbitrary artifacts.
- Requirements:
  - Expose report-first presentation for canonical final reports in execution detail surfaces.
  - Display related report summary, structured, and evidence artifacts separately from generic artifacts and observability surfaces.
  - Use artifact presentation contract fields for read target and renderer selection.
  - Treat optional execution convenience fields and any report projection endpoint as read models over normal artifacts.
- Owned coverage:
  - `DESIGN-REQ-011`: Requires server-defined latest-report behavior instead of browser heuristics.
  - `DESIGN-REQ-012`: Owns the Mission Control report panel/card behavior.
  - `DESIGN-REQ-013`: Owns viewer selection through the existing artifact presentation contract.
  - `DESIGN-REQ-014`: Presents evidence as separately openable linked artifacts.
  - `DESIGN-REQ-020`: Treats report convenience fields/projections as read models over artifacts.
  - `DESIGN-REQ-022`: Carries unresolved projection timing and multi-step projection choices as product questions.
- Assumptions:
  - The execution detail boot payload or API can expose enough report projection data for the UI to avoid local artifact guessing.

### STORY-004: Sensitive Report Access and Retention

- Short name: `report-access-retention`
- Source reference: `docs/Artifacts/ReportArtifacts.md`
- Source sections: 7. Consumer and producer invariants, 14. Security and access model, 15. Retention guidance
- Description: As an operator, I can rely on report artifacts to use existing authorization, preview, retention, pinning, and deletion behavior so sensitive reports remain useful without widening raw access.
- Independent test: Create sensitive report artifacts with restricted raw access and preview defaults, then assert authorized and unauthorized users see the correct preview/download behavior and retention/pinning metadata follows report policy.
- Dependencies: STORY-001
- Needs clarification: Should final reports be auto-pinned by default for selected workflow families?; Should report_type be a bounded enum for policy decisions or remain producer-defined with conventions first?
- Acceptance criteria:
  - Given a sensitive report has restricted raw access, then Mission Control uses preview/default-read behavior where available and does not assume full download is allowed.
  - Given report.primary or report.summary artifacts are created, then their default retention policy is long unless product policy overrides it.
  - Given report.structured or report.evidence artifacts are created, then their retention follows standard or long policy based on the report family and audit needs.
  - Given a final report is important to retain, then it can be pinned or unpinned through existing artifact APIs.
  - Deleting a report artifact uses artifact-system-native soft/hard deletion and does not implicitly delete unrelated runtime stdout, stderr, diagnostics, or other observability artifacts.
- Requirements:
  - Reuse the existing artifact authorization model for report artifacts and evidence.
  - Support preview artifacts and default_read_ref for sensitive report presentation.
  - Apply recommended retention mappings for primary, summary, structured, evidence, and related observability artifacts.
  - Keep deletion artifact-system-native without undefined cascading into unrelated observability artifacts.
- Owned coverage:
  - `DESIGN-REQ-015`: Owns safe degradation for sensitive report access and previews.
  - `DESIGN-REQ-016`: Owns retention, pinning, and deletion expectations.
  - `DESIGN-REQ-022`: Carries unresolved auto-pinning and report policy choices as bounded product questions.
- Assumptions:
  - Existing artifact authorization, preview, pinning, retention, and deletion APIs can be reused without a report-specific permission model.

### STORY-005: Report Workflow Rollout and Examples

- Short name: `report-rollout-examples`
- Source reference: `docs/Artifacts/ReportArtifacts.md`
- Source sections: 17. Example workflow mappings, 18. Suggested API/UI extensions, 19. Migration guidance, 20. Open questions
- Description: As a MoonMind maintainer, I can migrate report-producing workflow families incrementally to the report artifact contract with documented examples and graceful fallback for existing generic outputs.
- Independent test: Convert one representative workflow family, such as unit-test reports, to emit report.* artifacts while preserving existing generic output behavior for non-report workflows; verify UI/API fallback works when only output.primary exists.
- Dependencies: STORY-001, STORY-002, STORY-003
- Needs clarification: Should report.export distinguish PDF and HTML exports from editable/source-format report artifacts?; Should report evidence support bounded grouping metadata such as finding_id or section_id?
- Acceptance criteria:
  - Unit-test, coverage, pentest/security, and benchmark report examples each map to report.primary and appropriate summary, structured, evidence, runtime, and diagnostic artifacts.
  - New report-producing workflows prefer report.* semantics while existing generic output.primary flows continue to operate.
  - The UI degrades gracefully when an execution has only generic output artifacts and no report.primary artifact.
  - Migration docs or examples distinguish curated reports, evidence, runtime stdout/stderr, and diagnostics for each workflow family.
  - Convenience report fields or endpoints, if introduced during rollout, remain projections over the normal artifact APIs.
- Requirements:
  - Document and validate report mappings for unit-test, coverage, pentest/security, and benchmark workflows.
  - Support incremental rollout phases: metadata conventions, explicit report.* link types and UI surfacing, compact report bundle contract, and optional projections/filters/retention/pinning.
  - Keep existing generic outputs available during migration.
  - Preserve clear separation between curated reports and observability artifacts in examples and tests.
- Owned coverage:
  - `DESIGN-REQ-003`: Keeps migration examples within scope and excludes non-goals.
  - `DESIGN-REQ-007`: Ensures examples preserve report/evidence/observability separation.
  - `DESIGN-REQ-019`: Owns workflow family example mappings.
  - `DESIGN-REQ-020`: Ensures any convenience API is a projection over artifacts.
  - `DESIGN-REQ-021`: Owns incremental migration and graceful fallback behavior.
  - `DESIGN-REQ-022`: Tracks unresolved export/evidence grouping/report-type questions for later product decisions.
- Assumptions:
  - At least one existing or sample workflow can be used as the first vertical rollout target without requiring all report workflow families at once.

## Coverage Matrix

- `DESIGN-REQ-001` -> STORY-001
- `DESIGN-REQ-002` -> STORY-001
- `DESIGN-REQ-003` -> STORY-001, STORY-005
- `DESIGN-REQ-004` -> STORY-001
- `DESIGN-REQ-005` -> STORY-001
- `DESIGN-REQ-006` -> STORY-002
- `DESIGN-REQ-007` -> STORY-005
- `DESIGN-REQ-008` -> STORY-002
- `DESIGN-REQ-009` -> STORY-001
- `DESIGN-REQ-010` -> STORY-002
- `DESIGN-REQ-011` -> STORY-003
- `DESIGN-REQ-012` -> STORY-003
- `DESIGN-REQ-013` -> STORY-003
- `DESIGN-REQ-014` -> STORY-002, STORY-003
- `DESIGN-REQ-015` -> STORY-004
- `DESIGN-REQ-016` -> STORY-004
- `DESIGN-REQ-017` -> STORY-002
- `DESIGN-REQ-018` -> STORY-002
- `DESIGN-REQ-019` -> STORY-005
- `DESIGN-REQ-020` -> STORY-003, STORY-005
- `DESIGN-REQ-021` -> STORY-005
- `DESIGN-REQ-022` -> STORY-003, STORY-004, STORY-005

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-001
- `STORY-005` depends on: STORY-001, STORY-002, STORY-003

## Out Of Scope

- PDF rendering engines and document-conversion implementation details remain outside this breakdown because the design only requires report artifact semantics and viewer fallback behavior.
- Provider-specific report-generation prompts remain outside this breakdown because producers should integrate through artifact contracts, not hard-coded prompt behavior.
- Full-text search/indexing for report bodies remains outside this breakdown because report content stays artifact-backed and searchable behavior is not part of the first contract.
- Legal or compliance review procedures for report content remain outside this breakdown because the artifact system only governs storage, linkage, access, and presentation.
- A separate report storage system, mutable in-place report updates, mandatory PDF output, and parsing provider-native raw payloads as canonical reports are explicitly excluded by the source design.

## Coverage Gate

PASS - every major design point is owned by at least one story.
