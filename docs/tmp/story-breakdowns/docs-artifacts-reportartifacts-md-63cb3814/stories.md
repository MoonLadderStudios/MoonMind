# Report Artifacts Story Breakdown

- Source design: `docs/Artifacts/ReportArtifacts.md`
- Original source reference path: `docs/Artifacts/ReportArtifacts.md`
- Story extraction date: 2026-04-23T07:20:15Z
- Requested output mode: jira
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

ReportArtifacts.md defines report-producing workflows as first-class artifact-backed deliverables layered on the existing artifact system. It standardizes report link types, compact bundle refs, bounded metadata, execution and step linkage, report-first Mission Control presentation, access and retention expectations, and staged rollout guidance while keeping report content, evidence, logs, diagnostics, and raw provider payloads separate.

## Coverage Points

- **DESIGN-REQ-001 - Report-specific ownership boundary** (constraint, 1 Purpose, 1.1 Related docs and ownership boundaries): The document owns report-specific artifact contracts and presentation while existing docs own storage internals, generic presentation, live logs, and runtime result contracts.
- **DESIGN-REQ-002 - No separate report storage** (architecture, 3 Core decision, 11.1 Storage): Reports are stored in the existing immutable artifact system and index, not a new report blob store.
- **DESIGN-REQ-003 - Reports as first-class artifact family** (requirement, 3 Core decision, 4 Goals): MoonMind must represent report-producing workflows as clear, queryable end states using report-specific artifact semantics.
- **DESIGN-REQ-004 - Report bundle shape** (artifact, 3 Core decision, 6.2 Report bundle, 9 Report bundle model): A report deliverable usually consists of distinct primary, summary, structured, and evidence artifacts connected through a compact bundle result.
- **DESIGN-REQ-005 - Curated report separation** (constraint, 3 Core decision, 7 Consumer and producer invariants, 13 Relationship to observability and diagnostics): Reports, supporting evidence, observability logs, diagnostics, provider snapshots, and session continuity artifacts must remain related but distinct surfaces.
- **DESIGN-REQ-006 - Artifact-backed workflow safety** (constraint, 3 Core decision, 7 Consumer and producer invariants, 16 Workflow integration guidance): Large report bodies, screenshots, findings, logs, evidence, raw URLs, and transcripts must stay artifact-backed while workflows pass refs and bounded summaries.
- **DESIGN-REQ-007 - Report definitions and scopes** (state-model, 6 Definitions): The model distinguishes report artifacts, report bundles, evidence artifacts, final reports, and intermediate reports.
- **DESIGN-REQ-008 - Stable report link types** (contract, 8 Recommended report artifact classes): Report-producing flows should use report.primary, report.summary, report.structured, report.evidence, and optional report appendix/index/export link types.
- **DESIGN-REQ-009 - Generic output relationship** (contract, 8.3 Relationship to existing output classes, 5 Non-goals): Explicit report deliverables use report.* link types while generic non-report outputs keep output.* classes and not every output.primary is a report.
- **DESIGN-REQ-010 - Compact report bundle contract** (contract, 9 Report bundle model): Report-producing workflows should return report_bundle_v, artifact refs, report type, scope, sensitivity, and bounded counts only.
- **DESIGN-REQ-011 - Bounded safe report metadata** (security, 10 Metadata model for report artifacts): Report metadata uses standardized bounded keys and must exclude secrets, raw grants, cookies, tokens, and large inline payloads.
- **DESIGN-REQ-012 - Execution and step linkage** (integration, 11.2 Execution linkage, 11.3 Step-aware linkage): Report artifacts link to executions through namespace, workflow_id, run_id, link_type, label, and optional bounded step metadata.
- **DESIGN-REQ-013 - Server-owned latest report semantics** (state-model, 11.4 Latest report semantics): Latest report selection is server query or projection behavior, not mutable state or browser-side artifact sorting.
- **DESIGN-REQ-014 - Report-first Mission Control UX** (requirement, 12 Presentation rules): Mission Control should surface canonical report.primary artifacts directly, show related report content, and fall back to generic artifact lists when no report exists.
- **DESIGN-REQ-015 - Default read and renderer behavior** (contract, 12.2 Default read behavior, 12.3 Recommended renderer behavior): UI readers use default_read_ref, content type, render_hint, and metadata to choose viewers while raw download is exposed only when allowed.
- **DESIGN-REQ-016 - Evidence presentation** (requirement, 7 Consumer and producer invariants, 12.5 Evidence presentation): Evidence remains separately addressable and viewable instead of being irreversibly buried inside one rendered report file.
- **DESIGN-REQ-017 - Authorization and preview safety** (security, 14 Security and access model): Sensitive reports use existing artifact authorization and default_read_ref previews so useful presentation does not widen raw-access permissions.
- **DESIGN-REQ-018 - Retention, pinning, and deletion** (lifecycle, 15 Retention guidance): Report artifacts receive longer retention guidance, final reports should be easy to pin, and deletion remains artifact-system-native without implicit unrelated cascades.
- **DESIGN-REQ-019 - Activity-owned publishing** (architecture, 16 Workflow integration guidance): Activities assemble, create, link, and finalize report artifacts while workflow code orchestrates and receives compact refs.
- **DESIGN-REQ-020 - Canonical final report marking** (requirement, 16.3 Finalization rule): When a report is the primary deliverable, one artifact is clearly marked with report.primary, is_final_report=true, and report_scope=final.
- **DESIGN-REQ-021 - Multiple workflow family support** (requirement, 4 Goals, 17 Example workflow mappings): Unit-test, coverage, pentest, benchmark, compliance, and investigation workflows should share one report contract without a single forced producer schema.
- **DESIGN-REQ-022 - Report-aware convenience surfaces** (integration, 18 Suggested API/UI extensions): Execution detail summaries and an optional report projection endpoint can expose has_report, latest refs, report type/status, and bounded counts over normal artifacts.
- **DESIGN-REQ-023 - Incremental migration path** (migration, 19 Migration guidance): MoonMind should roll out report support without a flag-day migration, allowing generic outputs to continue while new workflows prefer report.* semantics.
- **DESIGN-REQ-024 - Documented product choices** (non-goal, 2 Scope / Non-goals, 5 Non-goals, 20 Open questions): PDF engines, provider prompts, full-text indexing, legal review, separate storage, mutable in-place updates, and provider-native payload parsing are excluded or deferred choices.

## Ordered Story Candidates

### STORY-001: Define the report artifact contract

- Short name: `report-artifact-contract`
- Jira issue type: Story
- Jira labels: report-artifacts, artifacts, contracts
- Source reference: `docs/Artifacts/ReportArtifacts.md` (1 Purpose; 1.1 Related docs and ownership boundaries; 3 Core decision; 6 Definitions; 8 Recommended report artifact classes; 9 Report bundle model; 10 Metadata model for report artifacts)
- Description: As a workflow producer, I want a canonical report artifact contract so report deliverables use explicit artifact semantics without introducing a second storage system.
- Independent test: Create report, generic output, and invalid large-metadata artifacts through the artifact contract layer; assert report.* link types, bundle refs, bounded metadata, and existing artifact storage/index behavior are used without a separate report store.
- Dependencies: None
- Needs clarification: None
- Acceptance criteria:
  - Report-specific link types are defined for report.primary, report.summary, report.structured, report.evidence, report.appendix, report.findings_index, and report.export where applicable.
  - Report artifacts persist through the existing artifact store and index with immutable artifact IDs.
  - Generic outputs continue to use output.primary, output.summary, and output.agent_result when they are not explicit report deliverables.
  - The compact report bundle shape carries artifact refs, report type, scope, sensitivity, and bounded counts only.
  - Metadata validation accepts standardized report keys and rejects or strips secrets, raw grants, cookies, session tokens, and large inline payloads.
  - Definitions for report artifact, report bundle, evidence artifact, final report, and intermediate report are reflected in code contracts or schema documentation.
- Requirements:
  - Model reports as first-class artifact families on existing artifact infrastructure.
  - Define stable report.* link-type semantics without treating every output.primary as a report.
  - Represent report bundles with refs and bounded metadata only.
  - Constrain report metadata to safe display fields.
- Source design coverage:
  - DESIGN-REQ-001: Keeps report-specific ownership aligned with related artifact, presentation, observability, and runtime docs.
  - DESIGN-REQ-002: Ensures no separate report storage system is introduced.
  - DESIGN-REQ-003: Establishes report-producing workflows as queryable report end states.
  - DESIGN-REQ-004: Defines primary, summary, structured, and evidence artifacts as one bundle.
  - DESIGN-REQ-007: Carries report, bundle, evidence, final, and intermediate definitions.
  - DESIGN-REQ-008: Owns stable report.* classes.
  - DESIGN-REQ-009: Preserves generic output behavior for non-report outputs.
  - DESIGN-REQ-010: Owns the compact workflow-facing bundle contract.
  - DESIGN-REQ-011: Owns bounded, safe metadata.
- Out of scope:
  - Implementing workflow publishing behavior.
  - Mission Control report rendering.
  - New persistent report storage.
- Jira handoff: Use this story as one future `/speckit.specify` input and preserve the source reference above.

### STORY-002: Publish report bundles from workflows

- Short name: `report-bundle-publishing`
- Jira issue type: Story
- Jira labels: report-artifacts, temporal, workflow-boundary
- Source reference: `docs/Artifacts/ReportArtifacts.md` (7 Consumer and producer invariants; 11 Storage and linkage rules; 16 Workflow integration guidance; 17 Example workflow mappings)
- Description: As an operator, I want report-producing workflows to publish immutable report bundles through activities so completed executions expose durable final and step-level reports without workflow-history bloat.
- Independent test: Run a representative report-producing workflow and a step-scoped report workflow; assert activities create and link report artifacts, workflow history contains only refs/bounded summaries, final report marking exists, and latest-report resolution is server-side.
- Dependencies: STORY-001
- Needs clarification: None
- Acceptance criteria:
  - Report assembly and artifact creation happen in activities, not workflow code.
  - Workflow return values and state include compact artifact refs and bounded metadata only.
  - Report artifacts link to namespace, workflow_id, run_id, link_type, label, and bounded step_id/attempt metadata when step-scoped.
  - A completed execution with a primary report has one canonical report.primary artifact with metadata.is_final_report=true and metadata.report_scope=final.
  - Intermediate reports can coexist with the final report without mutating prior report artifacts.
  - Latest report resolution is provided by server query/projection behavior and does not rely on browser-side sorting of arbitrary artifacts.
  - Unit-test, coverage, pentest/security, and benchmark-style flows can each publish valid bundles without adopting one universal findings schema.
- Requirements:
  - Create report artifacts through activity/service boundaries.
  - Link final and step-level reports to execution identity.
  - Keep report bodies, screenshots, findings, logs, and transcripts out of workflow history.
  - Support multiple workflow families under one report-bundle contract.
- Source design coverage:
  - DESIGN-REQ-005: Keeps curated reports distinct from observability and evidence.
  - DESIGN-REQ-006: Prevents large payloads and raw URLs in workflow state.
  - DESIGN-REQ-012: Owns execution and step linkage.
  - DESIGN-REQ-013: Owns server-side latest report behavior.
  - DESIGN-REQ-019: Owns activity-side publishing.
  - DESIGN-REQ-020: Owns canonical final report marking.
  - DESIGN-REQ-021: Validates the contract across representative workflow families.
- Out of scope:
  - Mission Control layout work.
  - Authorization/retention policy changes beyond linkage metadata.
- Jira handoff: Use this story as one future `/speckit.specify` input and preserve the source reference above.

### STORY-003: Surface canonical reports in Mission Control

- Short name: `mission-control-report-surface`
- Jira issue type: Story
- Jira labels: report-artifacts, mission-control, ui
- Source reference: `docs/Artifacts/ReportArtifacts.md` (12 Presentation rules; 13 Relationship to observability and diagnostics)
- Description: As a Mission Control user, I want executions with canonical reports to show a report-first surface with related evidence so I can inspect the deliverable without hunting through raw artifacts or logs.
- Independent test: Load execution details for runs with and without report.primary artifacts; assert report panel ordering, related report content, evidence rendering, viewer selection, raw-download permissions, and fallback to the generic artifact list.
- Dependencies: STORY-001, STORY-002
- Needs clarification: None
- Acceptance criteria:
  - Executions with a canonical report.primary show a Report panel or top-level report card before the generic artifact list.
  - The report surface shows summary metadata and an open action for the default read target.
  - Linked report.summary, report.structured, and report.evidence artifacts appear as related report content.
  - Artifacts, stdout, stderr, diagnostics, and other observability surfaces remain accessible outside the report panel.
  - Viewer choice uses default_read_ref, render_hint, content_type, and metadata.name/title.
  - Markdown, JSON, plain text, diff, image, PDF, and unknown binary report artifacts follow the documented renderer behavior.
  - Evidence artifacts remain individually addressable and viewable rather than being collapsed into the primary report by default.
  - When no report.primary exists, Mission Control falls back to the existing artifact list behavior.
- Requirements:
  - Add report-first execution detail presentation.
  - Use existing artifact presentation contracts for default reads and viewer selection.
  - Present related evidence separately from operational logs and diagnostics.
- Source design coverage:
  - DESIGN-REQ-005: Preserves report/evidence/observability separation in UI.
  - DESIGN-REQ-014: Owns report-first Mission Control behavior and fallback.
  - DESIGN-REQ-015: Owns default read and renderer behavior.
  - DESIGN-REQ-016: Owns individually addressable evidence presentation.
- Out of scope:
  - Changing artifact storage or workflow publishing contracts.
  - Adding a dedicated PDF renderer unless separately specified.
- Jira handoff: Use this story as one future `/speckit.specify` input and preserve the source reference above.

### STORY-004: Apply report access and lifecycle policy

- Short name: `report-access-lifecycle`
- Jira issue type: Story
- Jira labels: report-artifacts, security, retention
- Source reference: `docs/Artifacts/ReportArtifacts.md` (10 Metadata model for report artifacts; 14 Security and access model; 15 Retention guidance)
- Description: As an operator, I want sensitive reports to reuse artifact authorization, preview, retention, pinning, and deletion behavior so report delivery is useful without widening access or lifecycle risk.
- Independent test: Create restricted primary, summary, structured, and evidence report artifacts with preview refs and retention classes; assert raw access restrictions, default_read_ref preview behavior, metadata redaction, pin/unpin, and deletion semantics.
- Dependencies: STORY-001
- Needs clarification: Whether any workflow family should auto-pin final reports by default is product policy and remains unresolved.
- Acceptance criteria:
  - Report artifacts use the existing artifact authorization model for preview and raw reads.
  - Restricted report.primary, report.structured, and report.evidence artifacts can point default_read_ref to preview artifacts when raw access is disallowed.
  - Report metadata does not expose secrets, raw access grants, cookies, session tokens, or large inline payloads.
  - Default retention policy can map report.primary and report.summary to long retention, report.structured to long or standard, and report.evidence to standard or long by product policy.
  - Final reports can be explicitly pinned and unpinned through the existing artifact API.
  - Deleting a report artifact uses existing soft-delete/hard-delete behavior and does not implicitly delete unrelated observability artifacts.
- Requirements:
  - Reuse artifact authorization for sensitive reports.
  - Support preview-safe report presentation.
  - Apply report-specific retention recommendations through existing lifecycle controls.
  - Avoid unsafe cascading deletion semantics.
- Source design coverage:
  - DESIGN-REQ-011: Reinforces metadata safety for sensitive report surfaces.
  - DESIGN-REQ-017: Owns authorization and preview posture.
  - DESIGN-REQ-018: Owns retention, pinning, and artifact-native deletion.
- Out of scope:
  - New report-specific storage tables.
  - Legal/compliance review procedures for report content.
- Jira handoff: Use this story as one future `/speckit.specify` input and preserve the source reference above.

### STORY-005: Expose report-aware execution projections

- Short name: `report-execution-projections`
- Jira issue type: Story
- Jira labels: report-artifacts, api, projection
- Source reference: `docs/Artifacts/ReportArtifacts.md` (11.4 Latest report semantics; 18 Suggested API/UI extensions; 20 Open questions)
- Description: As an API consumer, I want execution detail responses and an optional report projection to expose the latest report refs and bounded counts so clients can build report-first views without duplicating artifact-selection heuristics.
- Independent test: Request execution details and the report projection for executions with no report, one final report, multiple report versions, and restricted artifacts; assert latest refs, bounded counts, authorization behavior, and no client-side heuristic dependency.
- Dependencies: STORY-001, STORY-002
- Needs clarification: Should the dedicated report projection endpoint ship in the first version, or should first version rely on execution-detail report fields plus standard artifact APIs?
- Acceptance criteria:
  - Execution detail data can expose has_report, latest_report_ref, latest_report_summary_ref, report_type, report_status, and bounded finding/severity counts.
  - A report projection endpoint, if implemented in this story, returns execution_ref, primary_report_ref, summary_ref, structured_ref, evidence_refs, report_type, and bounded counts over standard artifacts.
  - Projection output never becomes a second report storage model; underlying artifacts remain individually addressable.
  - Latest report selection is resolved server-side using execution identity and report link types.
  - Restricted artifacts still obey artifact authorization and preview/default-read behavior.
  - The story records whether the projection endpoint is implemented immediately or deferred in favor of execution-detail summary fields.
- Requirements:
  - Provide report-aware server selection for clients.
  - Keep projection data as a convenience read model over artifacts.
  - Make unresolved projection timing explicit.
- Source design coverage:
  - DESIGN-REQ-013: Prevents client-side latest-report guessing.
  - DESIGN-REQ-022: Owns execution summary fields and optional projection endpoint.
  - DESIGN-REQ-024: Captures the open choice around immediate projection endpoint delivery.
- Out of scope:
  - Full-text indexing of report bodies.
  - Replacing standard artifact APIs.
- Jira handoff: Use this story as one future `/speckit.specify` input and preserve the source reference above.

### STORY-006: Roll out report semantics without flag-day migration

- Short name: `report-rollout-compatibility`
- Jira issue type: Story
- Jira labels: report-artifacts, migration, documentation
- Source reference: `docs/Artifacts/ReportArtifacts.md` (2 Scope / Non-goals; 5 Non-goals; 17 Example workflow mappings; 19 Migration guidance; 20 Open questions; 21 Bottom line)
- Description: As a MoonMind maintainer, I want report artifact semantics to roll out incrementally with documented non-goals and producer examples so existing generic outputs continue working while new report workflows adopt report.* conventions.
- Independent test: Exercise old generic-output executions and new report.* executions side by side; assert old outputs still render through generic artifacts, new reports use report-first behavior, and documentation/tracker notes identify deferred choices without introducing specs or runtime aliases.
- Dependencies: STORY-001
- Needs clarification: Should report_type become a bounded enum or remain producer-defined by convention first?; Should report.export distinguish PDF/HTML exports from source-format reports?; Should evidence metadata include finding_id or section_id grouping semantics?
- Acceptance criteria:
  - Existing generic output.primary workflows continue to function without being reclassified as reports.
  - New report workflows prefer report.* link types and report metadata conventions.
  - Migration can proceed through metadata conventions, explicit link types/UI surfacing, compact bundle results, and later projections/filters/retention/pinning.
  - PDF rendering engines, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing remain out of scope unless separately specified.
  - Examples document unit-test, coverage, pentest/security, and benchmark report mappings.
  - Unresolved product choices around report_type enums, auto-pinning, projection endpoint timing, export semantics, evidence grouping, and multi-step task projections are tracked as clarification points for later stories.
- Requirements:
  - Support incremental report rollout with generic-output compatibility.
  - Document representative workflow mappings and explicit non-goals.
  - Keep migration notes under docs/tmp when implementation tracking is needed.
- Source design coverage:
  - DESIGN-REQ-021: Owns representative workflow-family examples.
  - DESIGN-REQ-023: Owns no-flag-day rollout behavior.
  - DESIGN-REQ-024: Owns non-goals and documented open product choices.
- Out of scope:
  - Creating implementation specs during breakdown.
  - Provider-specific report-generation prompts.
- Jira handoff: Use this story as one future `/speckit.specify` input and preserve the source reference above.

## Coverage Matrix

- **DESIGN-REQ-001** -> STORY-001
- **DESIGN-REQ-002** -> STORY-001
- **DESIGN-REQ-003** -> STORY-001
- **DESIGN-REQ-004** -> STORY-001
- **DESIGN-REQ-005** -> STORY-002, STORY-003
- **DESIGN-REQ-006** -> STORY-002
- **DESIGN-REQ-007** -> STORY-001
- **DESIGN-REQ-008** -> STORY-001
- **DESIGN-REQ-009** -> STORY-001
- **DESIGN-REQ-010** -> STORY-001
- **DESIGN-REQ-011** -> STORY-001, STORY-004
- **DESIGN-REQ-012** -> STORY-002
- **DESIGN-REQ-013** -> STORY-002, STORY-005
- **DESIGN-REQ-014** -> STORY-003
- **DESIGN-REQ-015** -> STORY-003
- **DESIGN-REQ-016** -> STORY-003
- **DESIGN-REQ-017** -> STORY-004
- **DESIGN-REQ-018** -> STORY-004
- **DESIGN-REQ-019** -> STORY-002
- **DESIGN-REQ-020** -> STORY-002
- **DESIGN-REQ-021** -> STORY-002, STORY-006
- **DESIGN-REQ-022** -> STORY-005
- **DESIGN-REQ-023** -> STORY-006
- **DESIGN-REQ-024** -> STORY-005, STORY-006

## Dependencies

- STORY-001 depends on no prior stories.
- STORY-002 depends on STORY-001.
- STORY-003 depends on STORY-001, STORY-002.
- STORY-004 depends on STORY-001.
- STORY-005 depends on STORY-001, STORY-002.
- STORY-006 depends on STORY-001.

## Out-of-Scope Items and Rationale

- **PDF rendering engines or document-conversion implementation details**: The design explicitly excludes renderer implementation details; report.export and PDF viewing can be specified later.
- **Provider-specific report-generation prompts**: The report contract must remain producer-neutral and avoid vendor-specific prompt coupling.
- **Full-text search or indexing strategy for report bodies**: Search/indexing is a separate product surface and not needed to establish report artifact semantics.
- **Legal/compliance review procedures for report content**: Policy review is outside the artifact contract and presentation model.
- **Separate report storage system**: The core decision requires reports to remain artifact-backed in the existing store.
- **Mutable in-place report updates**: Reports are immutable; revised reports create new artifact IDs and latest selection is query behavior.
- **Provider-native raw payload parsing as canonical reports**: Mission Control should consume report artifacts and metadata rather than treating provider payloads as reports.

## Unresolved Clarifications

- [STORY-004] Whether any workflow family should auto-pin final reports by default is product policy and remains unresolved.
- [STORY-005] Should the dedicated report projection endpoint ship in the first version, or should first version rely on execution-detail report fields plus standard artifact APIs?
- [STORY-006] Should report_type become a bounded enum or remain producer-defined by convention first?
- [STORY-006] Should report.export distinguish PDF/HTML exports from source-format reports?
- [STORY-006] Should evidence metadata include finding_id or section_id grouping semantics?

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
