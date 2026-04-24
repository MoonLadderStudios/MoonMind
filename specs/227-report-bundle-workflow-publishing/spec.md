# Feature Specification: Report Bundle Workflow Publishing

**Feature Branch**: `227-report-bundle-workflow-publishing`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-461 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-461 from MM project
Summary: Report Bundle Workflow Publishing
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-461 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-461: Report Bundle Workflow Publishing

Short Name
report-bundle-workflow-publishing

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 3. Core decision, 6. Definitions, 7. Consumer and producer invariants, 9. Report bundle model, 11.2 Execution linkage, 11.3 Step-aware linkage, 16. Workflow integration guidance, 16.3 Finalization rule
- Coverage IDs: DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-017, DESIGN-REQ-018

User Story
As a workflow author, I can publish a report bundle from activities and return compact refs to workflow code, so report bodies and evidence remain durable without bloating workflow history.

Acceptance Criteria
- Given an activity creates a report bundle, then it writes each report component as an artifact and links it to namespace, workflow_id, run_id, link_type, and optional label.
- Given a report is step-scoped or iterative, then bounded step metadata such as step_id, attempt, and scope is attached without embedding report content in workflow history.
- Given a report is the final deliverable, then exactly one canonical final report is identifiable via report.primary, metadata.is_final_report = true, and metadata.report_scope = final.
- Given evidence such as screenshots, command results, transcripts, excerpts, or structured findings exists, then it remains separately addressable instead of being buried only inside a rendered report.
- Workflow return values and persisted workflow state contain artifact_ref_v/artifact_id refs and bounded counts, not report bodies, evidence blobs, logs, screenshots, or raw download URLs.

Requirements
- Standardize a compact `report_bundle_v = 1` result shape with refs for `primary_report_ref`, `summary_ref`, `structured_ref`, `evidence_refs`, `report_type`, `report_scope`, `sensitivity`, and bounded counts.
- Keep report body, finding details, screenshots, logs, transcripts, and evidence artifact-backed.
- Make activities responsible for assembling report content, writing artifacts, linking artifacts, and returning compact bundles.
- Support execution-level and step-aware report linkage using existing artifact link semantics.

Relevant Implementation Notes
- Preserve MM-461 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Artifacts/ReportArtifacts.md` as the source design reference for report bundle shape, report artifact definitions, producer and consumer invariants, execution linkage, step-aware linkage, and final report identification.
- Scope implementation to activity-side report bundle publication and compact workflow return values; do not embed report bodies, evidence blobs, logs, screenshots, transcripts, raw download URLs, or large structured findings in workflow history or persisted workflow state.
- Report bundle publication should write each report component as an artifact, link it to the producing execution with namespace, workflow_id, run_id, link_type, and optional label, and return only artifact refs and bounded metadata to workflow code.
- Step-scoped or iterative reports should attach bounded step metadata such as step_id, attempt, and scope through artifact metadata or link metadata rather than inline report content.
- Final deliverables must make exactly one canonical final report identifiable through `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
- Evidence artifacts should remain separately addressable from rendered report content so screenshots, command results, transcripts, excerpts, and structured findings can be inspected independently.

Validation
- Verify an activity can create a report bundle by writing report components as artifacts and linking them to namespace, workflow_id, run_id, link_type, and optional label.
- Verify compact workflow returns include `artifact_ref_v` or artifact ID refs plus bounded counts, and exclude report bodies, evidence blobs, logs, screenshots, raw download URLs, and large finding details.
- Verify step-scoped and iterative report bundles preserve bounded step metadata without embedding report content in workflow history.
- Verify final report bundles expose exactly one canonical final report through `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
- Verify evidence such as screenshots, command results, transcripts, excerpts, and structured findings remains separately addressable through artifact refs.

Needs Clarification
- None

Dependencies
- Trusted Jira link metadata at fetch time shows MM-461 blocks MM-460, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-461 is blocked by MM-462, whose embedded status is Backlog.

## Classification

- Input type: Single-story feature request.
- Selected mode: Runtime.
- Source design: `docs/Artifacts/ReportArtifacts.md` is treated as runtime source requirements because the Jira brief points at implementation behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-461 were found under `specs/`; specification is the first incomplete stage.

## User Story - Publish Report Bundles From Activities

**Summary**: As a workflow author, I want activities to publish complete report bundles as artifact-backed components and return compact refs to workflow code, so durable reports and evidence do not bloat workflow history.

**Goal**: Report-producing workflows can delegate report assembly, artifact writing, execution linkage, step-aware metadata, final-report marking, and compact bundle return values to activity boundaries.

**Independent Test**: Run a report-producing activity path that creates primary, summary, structured, and evidence artifacts, then verify the produced bundle contains only compact artifact refs and bounded metadata; execution and step linkage is present; exactly one final report is identifiable; and no report body, evidence blob, log content, raw URL, screenshot, transcript, or large finding detail is embedded in workflow state or return values.

**Acceptance Scenarios**:

1. **Given** an activity creates a report bundle, **When** the bundle is finalized, **Then** each report component is written as an artifact and linked with namespace, workflow_id, run_id, link_type, and optional label.
2. **Given** a report is step-scoped or iterative, **When** the activity publishes the report artifacts, **Then** bounded step metadata such as step_id, attempt, and scope is attached without embedding report content in workflow history.
3. **Given** a report is the final deliverable for an execution, **When** the bundle is published, **Then** exactly one canonical final report is identifiable through `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
4. **Given** supporting evidence exists, **When** the bundle is published, **Then** screenshots, command results, transcripts, excerpts, and structured findings remain separately addressable through artifact refs instead of only being buried inside a rendered report.
5. **Given** workflow code receives the activity result, **When** it persists or returns the report bundle, **Then** it contains `artifact_ref_v` or artifact ID refs plus bounded counts and excludes report bodies, evidence blobs, logs, screenshots, raw download URLs, transcripts, and large finding details.

### Edge Cases

- An activity creates multiple candidates for final report status in one bundle.
- A report bundle omits a primary report but includes summary, structured, or evidence artifacts.
- Step metadata is present for an execution-level final report or absent for a step-scoped report.
- Evidence artifacts are produced after the primary report and need stable linkage without mutating the primary report artifact.
- A producer attempts to return raw URLs or inline evidence bodies in the compact bundle payload.

## Assumptions

- The report link types and bounded report metadata contract from MM-460 are available as the underlying artifact contract for this story.
- Activities, rather than workflow code, own report content assembly and artifact publication at runtime.
- Existing artifact storage, execution linkage, authorization, lifecycle, and preview/download behavior remain the boundary for report components.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/Artifacts/ReportArtifacts.md` §3, §6, §9 | A report is usually a bundle of primary report, summary, structured results, and evidence artifacts, and workflow-facing report bundle results must remain compact. | In scope | FR-001, FR-006 |
| DESIGN-REQ-008 | `docs/Artifacts/ReportArtifacts.md` §7 | Report bodies and large supporting evidence must remain artifact-backed, while logs, diagnostics, provider snapshots, and observability outputs remain separate from curated reports. | In scope | FR-002, FR-007 |
| DESIGN-REQ-010 | `docs/Artifacts/ReportArtifacts.md` §9 | Report bundle return values must use `report_bundle_v = 1`, artifact refs, bounded metadata, and bounded counts rather than report bodies or evidence blobs. | In scope | FR-006, FR-007 |
| DESIGN-REQ-014 | `docs/Artifacts/ReportArtifacts.md` §11.2 | Report artifacts must be linked to the producing execution with namespace, workflow_id, run_id, link_type, and optional label. | In scope | FR-003 |
| DESIGN-REQ-017 | `docs/Artifacts/ReportArtifacts.md` §11.3 | Step-scoped and iterative reports must use bounded step metadata such as step_id, attempt, and scope so clients do not infer report identity through local heuristics. | In scope | FR-004 |
| DESIGN-REQ-018 | `docs/Artifacts/ReportArtifacts.md` §16.1-§16.3 | Workflows must publish reports through activities that create and finalize artifacts, return compact refs, and mark exactly one canonical final report when the report is the primary deliverable. | In scope | FR-001, FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide an activity-side report bundle publication path that assembles report content, writes report artifacts, links them to the producing execution, and returns a compact report bundle to workflow code.
- **FR-002**: The system MUST keep report bodies, finding details, screenshots, logs, transcripts, and evidence content artifact-backed instead of embedding them in workflow history, workflow state, or activity return payloads.
- **FR-003**: The system MUST link each report bundle artifact to namespace, workflow_id, run_id, link_type, and optional label using the existing artifact linkage model.
- **FR-004**: The system MUST support step-scoped or iterative reports by attaching bounded step metadata such as step_id, attempt, and scope without embedding report content.
- **FR-005**: The system MUST ensure exactly one canonical final report is identifiable for a final report bundle through `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final`.
- **FR-006**: The system MUST standardize a compact `report_bundle_v = 1` result shape containing refs for primary report, summary, structured results, evidence, report type, report scope, sensitivity, and bounded counts.
- **FR-007**: The system MUST reject or fail validation for report bundle return values that contain report bodies, evidence blobs, logs, screenshots, raw download URLs, transcripts, or large finding details.
- **FR-008**: The system MUST keep screenshots, command results, transcripts, excerpts, and structured findings separately addressable through artifact refs when they are part of report evidence.
- **FR-009**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-461.

### Key Entities

- **Report Bundle**: A compact workflow-facing result for one report-producing activity or execution, containing artifact refs and bounded metadata for primary report, summary, structured output, evidence, report type, report scope, sensitivity, and counts.
- **Report Component Artifact**: An existing artifact that represents one piece of a report bundle, such as the primary report, summary, structured result, or evidence item.
- **Step Metadata**: Bounded metadata such as step_id, attempt, and scope that lets consumers associate report artifacts with step-scoped or iterative work without parsing report content.
- **Final Report Marker**: The combination of `report.primary`, `metadata.is_final_report = true`, and `metadata.report_scope = final` that identifies the canonical final report.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of report bundle publication tests verify component artifacts are linked with namespace, workflow_id, run_id, link_type, and optional label.
- **SC-002**: 100% of compact report bundle results use `report_bundle_v = 1` and contain only artifact refs, bounded metadata, and bounded counts.
- **SC-003**: 100% of report bundle validation tests reject embedded report bodies, evidence blobs, logs, screenshots, transcripts, raw download URLs, and large finding details in workflow-facing payloads.
- **SC-004**: Final report bundle tests verify exactly one canonical final report marker for every final report deliverable.
- **SC-005**: Evidence-addressability tests verify screenshots, command results, transcripts, excerpts, and structured findings remain separately reachable through artifact refs when present.
- **SC-006**: MM-461 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
