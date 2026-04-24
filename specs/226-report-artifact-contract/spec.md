# Feature Specification: Report Artifact Contract

**Feature Branch**: `226-report-artifact-contract`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-460 as the canonical Moon Spec orchestration input.

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

Jira issue: MM-460 from MM project
Summary: Report Artifact Contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-460 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-460: Report Artifact Contract

Short Name
report-artifact-contract

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 1. Purpose, 1.1 Related docs and ownership boundaries, 2. Scope / Non-goals, 3. Core decision, 5. Non-goals, 8. Recommended report artifact classes, 10. Metadata model for report artifacts, 11. Storage and linkage rules, 21. Bottom line
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-009

User Story
As a workflow producer, I can publish report deliverables using explicit report artifact link types and bounded metadata in the existing artifact system, so reports become first-class without creating a separate storage plane.

Acceptance Criteria
- Given a report-producing workflow publishes a canonical report, when the artifact is linked, then it uses `link_type = report.primary` and remains stored in the existing artifact store.
- Given summary, structured results, evidence, appendix, findings-index, or export artifacts are part of a report deliverable, then they use the corresponding `report.*` link type instead of generic output classes.
- Given report metadata is stored, then only bounded display and classification fields such as `artifact_type`, `report_type`, `report_scope`, `title`, `producer`, `subject`, `render_hint`, `counts`, `step_id`, and `attempt` are accepted for control-plane use.
- Given metadata contains secrets, raw access grants, cookies, session tokens, or large inline payloads, then publication rejects or redacts those fields according to the existing artifact boundary.
- Generic `output.primary`, `output.summary`, and `output.agent_result` flows continue to work for non-report deliverables.

Requirements
- Define stable report link types for `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export`.
- Represent reports as artifact families in the existing artifact system, not as a new storage system.
- Standardize bounded report metadata keys while keeping detailed findings and large content in artifacts.
- Keep report bodies and large supporting evidence artifact-backed instead of embedding them in workflow history or control-plane metadata.
- Keep report semantics explicit without breaking generic output flows.

Relevant Implementation Notes
- Layer report-specific behavior on top of `docs/Temporal/WorkflowArtifactSystemDesign.md` and `docs/Temporal/ArtifactPresentationContract.md`; do not redefine artifact identity, storage, authorization, lifecycle, generic preview behavior, or rendering rules.
- Use `report.*` link types only when an artifact is explicitly part of a report deliverable.
- Continue using `output.primary`, `output.summary`, and `output.agent_result` for generic non-report outputs.
- Store report artifacts in the existing artifact store and index with immutable artifact IDs and ordinary execution linkage.
- Link report artifacts to the producing execution using standard execution references and `link_type` values such as `report.primary`, `report.summary`, `report.structured`, and `report.evidence`.
- Keep standardized metadata bounded and safe for control-plane display; detailed findings belong in `report.structured` or other linked artifacts.
- Preserve separation between curated reports and observability surfaces such as stdout, stderr, merged logs, diagnostics, provider snapshots, and session continuity artifacts.

Non-Goals
- Introducing a separate report blob store or replacing generic artifact APIs with a report-specific storage system.
- Treating every `output.primary` artifact as a report.
- Storing evidence, logs, diagnostics, or large report bodies inline inside one payload by default.
- Introducing mutable in-place report updates.
- Requiring every report workflow to produce PDF output.
- Making Mission Control parse provider-native raw payloads as canonical reports.
- Implementing provider-specific report-generation prompts, PDF rendering, full-text indexing, legal/compliance review, or mutable report update workflows.

Validation
- Verify report-producing workflows can publish a canonical `report.primary` artifact stored in the existing artifact system.
- Verify report bundle artifacts use explicit `report.*` link types for primary report, summary, structured results, evidence, appendix, findings index, and export outputs.
- Verify bounded report metadata accepts only safe display and classification keys and rejects or redacts secrets, raw grants, cookies, session tokens, and large inline payloads.
- Verify generic `output.primary`, `output.summary`, and `output.agent_result` flows continue to work for non-report deliverables.
- Verify report artifacts remain linked through existing execution artifact references without creating a separate storage plane.

Needs Clarification
- None

## User Story - Publish Report Artifacts

**Summary**: As a workflow producer, I want to publish report deliverables using explicit report artifact link types and bounded metadata so consumers can distinguish reports from generic outputs without a separate storage plane.

**Goal**: Report-producing workflows can identify primary reports, summaries, structured report data, evidence, appendices, findings indexes, and exports through stable artifact linkage while preserving existing generic output behavior for non-report deliverables.

**Independent Test**: Create artifacts through the existing artifact service using report-specific and generic link types, then verify report link types and bounded metadata are accepted, unsafe report metadata is rejected, latest-report lookup works through existing execution linkage, and generic output links remain accepted.

**Acceptance Scenarios**:

1. **Given** a report-producing workflow creates a final human-facing report artifact, **When** it links that artifact to an execution, **Then** it uses `report.primary` and the artifact remains stored in the existing artifact system.
2. **Given** a report-producing workflow creates related summary, structured results, evidence, appendix, findings-index, or export artifacts, **When** those artifacts are linked, **Then** each artifact uses the matching `report.*` link type.
3. **Given** a report artifact is created with display/classification metadata, **When** the metadata only contains bounded allowed keys, **Then** the artifact is accepted for control-plane use.
4. **Given** a report artifact metadata payload contains raw secrets, access grants, cookies, session tokens, or large inline values, **When** publication is attempted, **Then** the artifact boundary rejects the metadata before storing it.
5. **Given** a workflow publishes generic non-report output, **When** it uses `output.primary`, `output.summary`, or `output.agent_result`, **Then** the existing generic output flow continues to work and is not reclassified as a report.

### Edge Cases

- Report metadata uses unknown keys that could cause clients to infer unsupported semantics.
- Report metadata includes nested objects or lists with large inline payloads.
- Report metadata uses a secret-like key or secret-like value in nested content.
- A completed artifact is later linked with a report link type.
- Existing generic output artifacts are created with non-report link types.

## Assumptions

- The existing artifact store, execution linkage tables, authorization rules, lifecycle behavior, and preview/download model remain the storage and access boundary for reports.
- Runtime producers will use the existing artifact service or activity boundary to create and link report artifacts.
- The initial runtime slice validates link types and metadata at the artifact boundary; richer report-first UI presentation can build on the same contract later.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Artifacts/ReportArtifacts.md` §1, §3 | Reports are first-class artifact families in the existing artifact system, not a separate storage plane. | In scope | FR-001, FR-007 |
| DESIGN-REQ-002 | `docs/Artifacts/ReportArtifacts.md` §3, §11 | Report artifacts remain artifact-backed and linked to executions through the standard artifact link model. | In scope | FR-001, FR-008 |
| DESIGN-REQ-003 | `docs/Artifacts/ReportArtifacts.md` §8 | Report-producing workflows use stable `report.*` link types for primary, summary, structured, evidence, appendix, findings-index, and export artifacts. | In scope | FR-002, FR-003 |
| DESIGN-REQ-004 | `docs/Artifacts/ReportArtifacts.md` §8.3 | Generic `output.primary`, `output.summary`, and `output.agent_result` continue to work for non-report outputs. | In scope | FR-006 |
| DESIGN-REQ-005 | `docs/Artifacts/ReportArtifacts.md` §10 | Report metadata is bounded to safe display and classification keys; detailed findings and large content stay in artifacts. | In scope | FR-004, FR-005 |
| DESIGN-REQ-009 | `docs/Artifacts/ReportArtifacts.md` §2, §5 | PDF conversion, provider prompting, full-text indexing, legal review, mutable report updates, and treating all generic outputs as reports remain out of scope. | In scope as exclusions | FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow report-producing workflows to store report artifacts in the existing artifact store and link them to executions without creating a separate report storage mechanism.
- **FR-002**: The system MUST define and accept the stable report link types `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export`.
- **FR-003**: The system MUST reject unsupported `report.*` link types so producers cannot create unregistered report semantics.
- **FR-004**: The system MUST restrict report artifact metadata to bounded display and classification fields for control-plane use.
- **FR-005**: The system MUST reject report metadata that contains secret-like keys, raw access grant fields, cookies, session token fields, or large inline payload values.
- **FR-006**: The system MUST preserve existing generic output link types including `output.primary`, `output.summary`, and `output.agent_result` for non-report deliverables.
- **FR-007**: The system MUST avoid creating any report-specific database table, blob store, or storage backend.
- **FR-008**: The system MUST keep latest-report discovery compatible with existing execution artifact linkage by filtering on `(namespace, workflow_id, run_id, link_type)`.
- **FR-009**: The system MUST keep provider-specific report prompting, PDF rendering, full-text indexing, legal review, mutable report updates, and automatic reclassification of generic outputs out of scope.
- **FR-010**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-460.

### Key Entities

- **Report Link Type**: A stable execution artifact link classification for report deliverables, including `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export`.
- **Report Metadata**: Bounded artifact metadata for control-plane display and filtering, limited to approved display/classification keys and safe scalar or compact count values.
- **Report Artifact Family**: A set of existing artifacts linked to one execution through `report.*` link types.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of accepted `report.*` artifact links use one of the seven supported report link types.
- **SC-002**: 100% of report metadata payloads containing unsupported keys, secret-like keys/values, or inline string values over the configured report metadata limit are rejected before storage.
- **SC-003**: Existing generic output link tests for `output.primary`, `output.summary`, and `output.agent_result` continue to pass without report-specific metadata requirements.
- **SC-004**: Latest artifact lookup for `report.primary` returns the most recent primary report through the existing execution linkage query without any report-specific storage table.
- **SC-005**: MM-460 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
