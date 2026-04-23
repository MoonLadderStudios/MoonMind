# Feature Specification: Publish Report Bundles

**Feature Branch**: `245-publish-report-bundles`
**Created**: 2026-04-23
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-493 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-493-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: existing report-related feature directories in `specs/` map to different Jira stories (`MM-461`, `MM-492`), so no active Moon Spec feature directory matched MM-493 and `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-493 MoonSpec Orchestration Input

## Source

- Jira issue: MM-493
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish report bundles from workflows
- Labels:
  - `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-493 from MM project
Summary: Publish report bundles from workflows
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-493 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-493: Publish report bundles from workflows

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections:
  - 7 Consumer and producer invariants
  - 11 Storage and linkage rules
  - 16 Workflow integration guidance
  - 17 Example workflow mappings
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-021

User Story
As an operator, I want report-producing workflows to publish immutable report bundles through activities so completed executions expose durable final and step-level reports without workflow-history bloat.

Acceptance Criteria
- Report assembly and artifact creation happen in activities, not workflow code.
- Workflow return values and state include compact artifact refs and bounded metadata only.
- Report artifacts link to namespace, workflow_id, run_id, link_type, label, and bounded step_id/attempt metadata when step-scoped.
- A completed execution with a primary report has one canonical `report.primary` artifact with `metadata.is_final_report=true` and `metadata.report_scope=final`.
- Intermediate reports can coexist with the final report without mutating prior report artifacts.
- Latest report resolution is provided by server query/projection behavior and does not rely on browser-side sorting of arbitrary artifacts.
- Unit-test, coverage, pentest/security, and benchmark-style flows can each publish valid bundles without adopting one universal findings schema.

Requirements
- Create report artifacts through activity/service boundaries.
- Link final and step-level reports to execution identity.
- Keep report bodies, screenshots, findings, logs, and transcripts out of workflow history.
- Support multiple workflow families under one report-bundle contract.

Relevant Implementation Notes
- Preserve MM-493 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Keep report assembly and artifact creation out of workflow code; implement bundle creation in activities or service boundaries.
- Represent reports in workflow state and return values with compact artifact refs and bounded metadata only.
- Link final and step-level report artifacts to namespace, workflow ID, run ID, link type, label, and bounded step-scoped identifiers when present.
- Ensure the final execution report resolves to one canonical `report.primary` artifact marked with `metadata.is_final_report=true` and `metadata.report_scope=final`.
- Allow intermediate report artifacts to coexist without mutating or overwriting previously published artifacts.
- Implement latest-report lookup through server query or projection behavior rather than browser-side artifact sorting.
- Keep the report bundle contract flexible enough for unit-test, coverage, pentest/security, and benchmark-style workflows without forcing a universal findings schema.
- Keep report bodies, screenshots, findings, logs, and transcripts out of workflow history and store them only in artifact-backed report bundles.
- Use `docs/Artifacts/ReportArtifacts.md` as the design reference for report invariants, storage/linkage rules, workflow integration guidance, and example mappings.

Non-Goals
- Moving report assembly logic into workflow code.
- Requiring browser-side sorting heuristics to determine the latest canonical report.
- Forcing all workflow families to adopt one universal findings schema.
- Mutating prior report artifacts when publishing intermediate or final reports.

Validation
- Verify report assembly and artifact creation happen in activities or service boundaries, not workflow code.
- Verify workflow return values and persisted workflow state carry compact artifact refs and bounded metadata only.
- Verify final and step-level report artifacts are linked to execution identity with the required bounded metadata.
- Verify a completed execution with a primary report exposes one canonical `report.primary` artifact marked final with `metadata.report_scope=final`.
- Verify intermediate reports can coexist with the final report without mutating prior artifacts.
- Verify latest-report resolution comes from server query/projection behavior rather than browser-side sorting.
- Verify unit-test, coverage, pentest/security, and benchmark-style flows can each publish valid report bundles under the shared contract.
- Verify report bodies, screenshots, findings, logs, and transcripts stay out of workflow history.

Needs Clarification
- None
```

## User Story - Publish Immutable Report Bundles

**Summary**: As an operator, I want report-producing workflows to publish immutable report bundles through activities so completed executions expose durable final and step-level reports without workflow-history bloat.

**Goal**: Report-producing workflows publish durable report artifacts through activity-owned boundaries, expose compact workflow-safe references, and preserve one canonical final report while allowing intermediate and step-scoped reports to coexist.

**Independent Test**: Trigger a report-producing workflow path that publishes final and step-scoped report artifacts, then verify the workflow-visible result contains only compact refs and bounded metadata, exactly one canonical final report is identifiable, intermediate artifacts remain immutable, and latest report resolution comes from server behavior instead of browser-side sorting.

**Acceptance Scenarios**:

1. **Given** a report-producing workflow finishes a report publication step, **When** the system assembles and stores the report bundle, **Then** report assembly and artifact creation happen through activities or service boundaries rather than workflow code.
2. **Given** a workflow receives a published report bundle, **When** the workflow persists or returns the result, **Then** it carries only compact artifact refs and bounded metadata instead of report bodies, screenshots, findings, logs, or transcripts.
3. **Given** a report is scoped to an execution step or attempt, **When** the bundle is published, **Then** the linked artifacts preserve namespace, workflow identity, link type, label, and bounded step identifiers without embedding report content in workflow history.
4. **Given** an execution completes with a primary report, **When** consumers resolve the canonical final report, **Then** exactly one `report.primary` artifact is marked as the final report with explicit final-report metadata.
5. **Given** intermediate reports exist before or after the final report, **When** later reports are published, **Then** previously published report artifacts remain immutable and coexist with the final report.
6. **Given** a client needs the latest report for an execution or step scope, **When** it queries the system, **Then** latest report resolution is supplied by server-side behavior rather than browser-side sorting heuristics.
7. **Given** different MoonMind workflow families publish report bundles, **When** they follow the shared report-bundle contract, **Then** each flow can publish valid report bundles without being forced into one universal findings schema.

### Edge Cases

- A workflow publishes a final report and then later emits an intermediate or corrective report for the same scope.
- A step-scoped report omits bounded step metadata or tries to encode step context inside report content instead.
- A producer attempts to return raw report bodies, screenshots, transcripts, or large findings inline in workflow-visible bundle data.
- A client attempts to infer the latest report from artifact ordering or filenames instead of server-defined latest behavior.
- Different workflow families produce different structured findings shapes while still sharing the same artifact-backed report-bundle contract.

## Assumptions

- MM-492 remains the contract-defining story for canonical report artifact semantics, while MM-493 focuses on workflow publication behavior using that contract.
- Existing artifact storage and linkage infrastructure remains the system of record for report bundle durability and report identity.
- Report consumers continue to rely on server-defined latest behavior rather than browser-local heuristics once report bundles are published.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-005 | `docs/Artifacts/ReportArtifacts.md` §7 | Workflow-visible report state must remain compact and must not embed report bodies, screenshots, findings, logs, or transcripts inline. | In scope | FR-002, FR-006 |
| DESIGN-REQ-006 | `docs/Artifacts/ReportArtifacts.md` §7, §16 | Report-producing workflows must publish report artifacts through activity or service boundaries rather than workflow code. | In scope | FR-001 |
| DESIGN-REQ-012 | `docs/Artifacts/ReportArtifacts.md` §11 | Report artifacts must be linked to execution identity through namespace, workflow_id, run_id, link_type, label, and bounded step-scoped metadata when applicable. | In scope | FR-003, FR-004 |
| DESIGN-REQ-013 | `docs/Artifacts/ReportArtifacts.md` §11, §16 | Final and step-level reports must remain durable artifact-backed outputs rather than workflow-history payloads. | In scope | FR-002, FR-003 |
| DESIGN-REQ-019 | `docs/Artifacts/ReportArtifacts.md` §16, §17 | Report publication must support multiple workflow families under one shared report-bundle contract without forcing one universal findings schema. | In scope | FR-007 |
| DESIGN-REQ-020 | `docs/Artifacts/ReportArtifacts.md` §16 | Final report publication must preserve exactly one canonical final report marker for the relevant scope. | In scope | FR-005 |
| DESIGN-REQ-021 | `docs/Artifacts/ReportArtifacts.md` §7, §17 | Latest report resolution must come from server-defined behavior rather than client-side sorting or filename heuristics. | In scope | FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST publish report bundles through activity-owned or service-owned report publication boundaries rather than workflow code.
- **FR-002**: The system MUST keep workflow-visible report bundle state limited to compact artifact refs and bounded metadata rather than inline report bodies, screenshots, findings, logs, or transcripts.
- **FR-003**: The system MUST link final and step-level report artifacts to execution identity with namespace, workflow_id, run_id, link_type, and label when applicable.
- **FR-004**: The system MUST preserve bounded step-scoped metadata for step-level or attempt-level report bundles without embedding report content in workflow history.
- **FR-005**: The system MUST ensure a completed execution with a primary report exposes exactly one canonical final `report.primary` artifact marked as final for that scope.
- **FR-006**: The system MUST provide server-defined latest report resolution behavior so clients do not rely on browser-side sorting, filenames, or local heuristics to find the current report.
- **FR-007**: The system MUST allow unit-test, coverage, pentest/security, benchmark, and similar workflow families to publish valid report bundles under the shared contract without forcing one universal findings schema.
- **FR-008**: The system MUST keep intermediate and final report artifacts immutable so newer report publications do not mutate previously published artifacts.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this story MUST preserve Jira issue key MM-493.

### Key Entities

- **Report Bundle Publication**: The workflow-facing act of publishing one report bundle through an activity or service boundary while keeping report content artifact-backed.
- **Workflow-Safe Report Bundle State**: The compact workflow-visible state containing artifact references and bounded metadata for a published report bundle.
- **Canonical Final Report**: The single primary report artifact designated as the final report for a given execution or scope.
- **Step-Scoped Report Linkage**: The bounded execution and step identity attached to report artifacts so step-level reports remain traceable without embedding report content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves report publication paths create and store report artifacts through activities or service boundaries rather than workflow code.
- **SC-002**: Validation proves workflow-visible report bundle state contains only compact refs and bounded metadata, with no inline report bodies, screenshots, findings, logs, or transcripts.
- **SC-003**: Validation proves final and step-level report artifacts retain execution identity and bounded step metadata when applicable.
- **SC-004**: Validation proves every completed execution with a primary report exposes exactly one canonical final `report.primary` artifact for the relevant scope.
- **SC-005**: Validation proves later report publications do not mutate prior report artifacts and that intermediate reports can coexist with the final report.
- **SC-006**: Validation proves latest report resolution comes from server-defined behavior instead of browser-side sorting or filename heuristics.
- **SC-007**: Traceability review confirms MM-493 and DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-019, DESIGN-REQ-020, and DESIGN-REQ-021 remain preserved in MoonSpec artifacts and downstream implementation evidence.
