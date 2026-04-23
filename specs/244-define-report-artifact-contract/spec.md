# Feature Specification: Report Artifact Contract

**Feature Branch**: `244-define-report-artifact-contract`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-492 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-492-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing `specs/*` feature directory matched MM-492, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-492 MoonSpec Orchestration Input

## Source

- Jira issue: MM-492
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Define the report artifact contract
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-492 from MM project
Summary: Define the report artifact contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-492 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-492: Define the report artifact contract

User Story
As a workflow producer, I want a canonical report artifact contract so report deliverables use explicit artifact semantics without introducing a second storage system.

Source Document
`docs/Artifacts/ReportArtifacts.md`

Source Title
Report Artifacts

Source Sections
- 1 Purpose
- 1.1 Related docs and ownership boundaries
- 3 Core decision
- 6 Definitions
- 8 Recommended report artifact classes
- 9 Report bundle model
- 10 Metadata model for report artifacts

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-011

Acceptance Criteria
- Report-specific link types are defined for `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export` where applicable.
- Report artifacts persist through the existing artifact store and index with immutable artifact IDs.
- Generic outputs continue to use `output.primary`, `output.summary`, and `output.agent_result` when they are not explicit report deliverables.
- The compact report bundle shape carries artifact refs, report type, scope, sensitivity, and bounded counts only.
- Metadata validation accepts standardized report keys and rejects or strips secrets, raw grants, cookies, session tokens, and large inline payloads.
- Definitions for report artifact, report bundle, evidence artifact, final report, and intermediate report are reflected in code contracts or schema documentation.

Requirements
- Model reports as first-class artifact families on existing artifact infrastructure.
- Define stable `report.*` link-type semantics without treating every `output.primary` as a report.
- Represent report bundles with refs and bounded metadata only.
- Constrain report metadata to safe display fields.

Implementation Notes
- Keep report deliverables on the existing artifact infrastructure; do not introduce a second storage system.
- Preserve the distinction between explicit report deliverables and generic outputs.
- Keep the bundle contract compact and reference-based rather than embedding large payloads.
- Enforce metadata sanitization for secret-like values and oversized inline content.
- Reflect the canonical definitions from `docs/Artifacts/ReportArtifacts.md` in code contracts or schema documentation.

Needs Clarification
- None
```

## User Story - Define Report Deliverable Semantics

**Summary**: As a workflow producer, I want a canonical report artifact contract so report deliverables use explicit artifact semantics without introducing a second storage system.

**Goal**: Report-producing workflows publish canonical report artifacts, compact report bundles, and bounded metadata that distinguish curated reports from generic outputs, evidence, and observability artifacts.

**Independent Test**: Validate a report-producing workflow contract and a non-report generic output contract side by side, then confirm report deliverables use explicit `report.*` semantics, bundles contain only refs plus bounded metadata, standardized metadata rejects unsafe values, and core report definitions remain traceable in system contracts or schema-facing documentation.

**Acceptance Scenarios**:

1. **Given** a workflow publishes a report deliverable, **When** it classifies the produced artifacts, **Then** the canonical report and its related summary, structured result, evidence, appendix, findings index, and export artifacts use the appropriate `report.*` link types instead of generic output link types.
2. **Given** a workflow produces generic non-report output, **When** its artifacts are classified, **Then** `output.primary`, `output.summary`, and `output.agent_result` remain valid generic outputs and are not implicitly treated as reports.
3. **Given** a workflow exposes a report bundle, **When** downstream consumers inspect the bundle contract, **Then** it contains only artifact refs plus bounded metadata such as report type, scope, sensitivity, and bounded counts rather than inline report bodies, screenshots, logs, or transcripts.
4. **Given** report artifacts expose metadata for display and filtering, **When** metadata is validated, **Then** only standardized bounded report keys are accepted and unsafe values such as secrets, raw grants, cookies, session tokens, and large inline payloads are rejected or stripped.
5. **Given** a consumer needs to identify the canonical report, **When** it resolves the latest report for a scope, **Then** it uses explicit report link semantics and server-defined report behavior rather than local heuristics over generic artifact names.
6. **Given** report deliverables include supporting evidence and observability artifacts, **When** consumers inspect the outputs, **Then** curated report content remains separate from evidence, logs, diagnostics, and other runtime observability surfaces.
7. **Given** a producer or consumer needs contract terminology, **When** it references report semantics, **Then** the definitions for report artifact, report bundle, evidence artifact, final report, and intermediate report are available in system contracts or schema-facing documentation.

### Edge Cases

- A workflow publishes `output.primary` and `report.evidence` but no `report.primary`.
- A report bundle attempts to include inline report bodies, screenshots, transcripts, or log payloads instead of refs.
- Report metadata includes secret-like values or oversized inline data.
- A client tries to infer the report from filenames or render hints alone.
- A report workflow emits observability artifacts without clearly separating them from curated report artifacts.
- A producer emits only a `report.summary` or `report.structured` artifact without a canonical human-facing report for the same scope.

## Assumptions

- The existing artifact system remains the canonical storage, identity, retention, and authorization layer for report deliverables.
- Generic artifact presentation and live observability contracts continue to own preview behavior and runtime diagnostics outside this story.
- MM-492 defines the contract semantics for reports; workflow-family rollout examples and migration sequencing are handled by separate report rollout stories.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Artifacts/ReportArtifacts.md` §3 | Reports must be first-class artifact families on the existing artifact infrastructure and must not introduce a separate report storage system. | In scope | FR-001, FR-002 |
| DESIGN-REQ-002 | `docs/Artifacts/ReportArtifacts.md` §8.1-§8.2 | Report-producing workflows must use stable `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `report.appendix`, `report.findings_index`, and `report.export` semantics where applicable. | In scope | FR-001, FR-003 |
| DESIGN-REQ-003 | `docs/Artifacts/ReportArtifacts.md` §8.3 | Generic `output.primary`, `output.summary`, and `output.agent_result` outputs remain valid for non-report deliverables and must not be reclassified as reports by default. | In scope | FR-004 |
| DESIGN-REQ-004 | `docs/Artifacts/ReportArtifacts.md` §9 | Report bundles must use a compact workflow-safe shape with artifact refs and bounded metadata only. | In scope | FR-005, FR-006 |
| DESIGN-REQ-007 | `docs/Artifacts/ReportArtifacts.md` §10 | Report artifacts should use a standardized bounded metadata vocabulary for display, filtering, and report classification. | In scope | FR-006, FR-007 |
| DESIGN-REQ-008 | `docs/Artifacts/ReportArtifacts.md` §10 | Report metadata must remain bounded and must not include secrets, raw access grants, cookies, session tokens, or large inline payloads. | In scope | FR-007 |
| DESIGN-REQ-009 | `docs/Artifacts/ReportArtifacts.md` §6 | Report artifact, report bundle, evidence artifact, final report, and intermediate report definitions must be available as canonical contract terminology. | In scope | FR-010 |
| DESIGN-REQ-010 | `docs/Artifacts/ReportArtifacts.md` §7 | Consumers must identify canonical reports through report-specific link semantics and server-defined latest behavior rather than local heuristics. | In scope | FR-008 |
| DESIGN-REQ-011 | `docs/Artifacts/ReportArtifacts.md` §7 | Curated report content, evidence artifacts, and observability artifacts must remain related but distinct surfaces. | In scope | FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST classify explicit report deliverables through stable `report.*` artifact link semantics on the existing artifact infrastructure.
- **FR-002**: The system MUST treat report deliverables as first-class artifact-backed outputs rather than as a separate report storage model.
- **FR-003**: The system MUST support canonical report, summary, structured result, evidence, appendix, findings index, and export artifact roles for report-producing workflows when those roles are applicable.
- **FR-004**: The system MUST preserve `output.primary`, `output.summary`, and `output.agent_result` as generic non-report outputs unless a producer explicitly publishes report semantics.
- **FR-005**: The system MUST expose a compact report bundle contract containing only artifact refs and bounded metadata for report type, report scope, sensitivity, and bounded counts.
- **FR-006**: The system MUST prevent report bundle contracts from embedding large report bodies, findings payloads, screenshots, logs, transcripts, or other large evidence inline.
- **FR-007**: The system MUST validate report artifact metadata against a standardized bounded report metadata vocabulary and reject or strip unsafe values.
- **FR-008**: The system MUST resolve canonical reports through report-specific link semantics and server-defined latest behavior rather than client-side heuristics over names or local display guesses.
- **FR-009**: The system MUST preserve separation between curated report artifacts, supporting evidence artifacts, and observability or diagnostics artifacts.
- **FR-010**: The system MUST make canonical report terminology available for report artifact, report bundle, evidence artifact, final report, and intermediate report concepts in contract-facing documentation or schema-facing materials.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this story MUST preserve Jira issue key MM-492.

### Key Entities

- **Report Artifact**: An immutable artifact whose primary purpose is to communicate workflow, step, or evaluation outcomes in human-readable or machine-readable report form.
- **Report Bundle**: A compact contract containing refs to the canonical report artifacts and bounded report metadata for one scope.
- **Evidence Artifact**: A separately addressable supporting artifact linked to a report deliverable.
- **Canonical Report Resolution**: The server-defined behavior that determines the current primary report for a scope without client-side guessing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove report-producing outputs can be classified with explicit `report.*` semantics and generic `output.*` outputs remain generic when report semantics are absent.
- **SC-002**: Tests prove compact report bundle contracts contain only refs and bounded metadata, not inline report bodies, screenshots, findings payloads, logs, or transcripts.
- **SC-003**: Tests prove report metadata validation accepts the standardized report metadata keys and rejects or strips unsafe values such as secrets, raw grants, cookies, session tokens, and oversized inline payloads.
- **SC-004**: Tests prove canonical report resolution uses explicit report link semantics and server-defined latest behavior rather than client-side naming heuristics.
- **SC-005**: Tests prove curated report artifacts remain distinct from evidence and observability artifacts in report-producing outputs.
- **SC-006**: Traceability review confirms MM-492 and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-011 remain preserved in MoonSpec artifacts and downstream implementation evidence.
