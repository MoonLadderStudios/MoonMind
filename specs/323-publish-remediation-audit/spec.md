# Feature Specification: Publish Remediation Audit Evidence

**Feature Branch**: `323-publish-remediation-audit`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-623 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-623 MoonSpec Orchestration Input

## Source

- Jira issue: MM-623
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish remediation audit artifacts, summaries, and queryable events
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose a normalized preset brief or recommended preset instructions.

## Canonical MoonSpec Feature Request

Jira issue: MM-623 from MM project
Summary: Publish remediation audit artifacts, summaries, and queryable events
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-623 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-623: Publish remediation audit artifacts, summaries, and queryable events

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 14. Artifacts, summaries, and audit
- Appendix B. Example remediation summary

Coverage IDs:
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-028

As an operator, I can inspect durable remediation evidence, decisions, actions, verification, summary blocks, target-side annotations, and compact audit events so that every diagnosis and intervention remains reviewable after the run completes.

Acceptance Criteria
- Every remediation run publishes the minimum required artifact set that applies to its path and uses remediation.* artifact types.
- Decision logs record attempted/skipped/denied/escalated repair candidates, action refs, verification refs, prevention refs or no-PR reasons.
- Run summary includes stable remediation fields such as target ids, mode, authority, actions, immediateRepair, prevention, resolution, evidenceDegraded, and escalated.
- Queryable audit events exist for side-effecting action decisions and contain bounded metadata only.
- Artifact presentation obeys normal preview/redaction rules.

Requirements
- Durable artifacts are the operator-facing evidence trail.
- Audit events are compact queryable control-plane records.
- Target-side mutation annotations do not replace subsystem-native artifacts.
"""

## User Story - Review Remediation Evidence

**Summary**: As an operator, I want each remediation run to publish durable evidence, summaries, target annotations, and compact audit records so that every diagnosis and intervention remains reviewable after the run completes.

**Goal**: Operators can reconstruct what a remediation run observed, decided, attempted, verified, summarized, and escalated without relying on transient logs or mutable target-side state.

**Independent Test**: Can be fully tested by completing representative remediation runs across diagnosis-only, repair-attempted, prevention-attempted, degraded-evidence, and escalated paths, then validating that the operator-visible evidence trail, summary fields, and queryable audit records describe the same bounded run history.

**Acceptance Scenarios**:

1. **Given** a remediation run completes with diagnosis-only output, **When** an operator inspects its evidence, **Then** the applicable remediation artifacts and run summary are present and identify why no repair action was needed.
2. **Given** a remediation run attempts or skips repair candidates, **When** an operator reviews the decision evidence, **Then** each attempted, skipped, denied, or escalated candidate includes bounded rationale and references to any action, verification, prevention, or no-PR evidence.
3. **Given** a remediation run performs a side-effecting action, **When** audit records are queried for that run, **Then** a compact event records the action decision with bounded metadata and no sensitive or raw storage data.
4. **Given** a remediation run mutates a target-managed session or workload, **When** the target and remediation evidence are inspected, **Then** target-side annotations supplement the subsystem-native artifacts without replacing them.
5. **Given** a remediation run has degraded evidence or escalates, **When** the run summary is inspected, **Then** the summary exposes the degraded or escalated state and stable remediation fields needed for review.
6. **Given** an operator previews remediation artifacts, **When** artifact presentation rules apply, **Then** only safe metadata, artifact references, and redacted preview content are visible.

### Edge Cases

- A remediation run may complete without any side-effecting action; the evidence trail must still explain the no-action outcome.
- Some artifact types apply only to paths that request or perform actions; missing non-applicable artifacts must not be reported as evidence loss.
- Evidence can be partially unavailable or degraded; the summary and audit trail must expose a bounded degraded reason instead of implying complete evidence.
- Target-managed systems can already publish their own control artifacts; remediation annotations must remain supplementary and must not overwrite or replace subsystem-native evidence.
- A prevention effort may not produce a pull request; the decision log must record the bounded reason.

## Assumptions

- Runtime intent is required because Jira Orchestrate selected a runtime implementation workflow and the brief describes product behavior, not documentation-only work.
- The cited Task Remediation sections are source requirements for the selected story, while unrelated remediation design sections are out of scope for this specification.
- Existing artifact preview, redaction, and authorization policies define the normal presentation rules that remediation artifacts must obey.
- Queryable audit events are expected to contain compact control-plane metadata rather than full artifact bodies or logs.

## Source Design Requirements

- **DESIGN-REQ-022** (`docs/Tasks/TaskRemediation.md` lines 1084-1115): Remediation runs must publish the applicable durable evidence artifacts with remediation artifact types, bounded safe metadata, artifact references instead of URLs, preview/redaction handling, and no secrets in metadata or bodies. Scope: in scope. Mapped to FR-001, FR-002, FR-007, and FR-008.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` lines 1117-1127 and 1476-1487): When remediation mutates a target-managed session or workload, target-side annotations or continuity artifacts must supplement, not replace, subsystem-native artifacts, and corrected or side-effecting decisions must remain auditable rather than silently mutating the original input. Scope: in scope. Mapped to FR-003, FR-005, FR-006, and FR-007.
- **DESIGN-REQ-028** (`docs/Tasks/TaskRemediation.md` lines 1129-1165 and 1434-1469): Remediation summaries must expose stable remediation fields, immediate repair and prevention outcomes when applicable, bounded resolution states, degraded-evidence state, and escalation state. Scope: in scope. Mapped to FR-004, FR-007, and FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST publish the minimum remediation evidence artifact set that applies to each completed remediation run path.
- **FR-002**: Remediation evidence artifacts MUST use remediation-specific artifact classifications so operators can identify context, plan, decision log, action request, action result, verification, and summary evidence.
- **FR-003**: Decision evidence MUST record attempted, skipped, denied, and escalated repair candidates with bounded rationale and references to any action, verification, prevention, or no-PR evidence.
- **FR-004**: Run summaries MUST expose stable remediation fields covering target identifiers, remediation mode, authority, attempted actions, immediate repair, prevention, resolution, degraded evidence, and escalation state when applicable.
- **FR-005**: The system MUST produce queryable audit records for side-effecting remediation action decisions using bounded metadata only.
- **FR-006**: Target-side mutation annotations MUST supplement subsystem-native target artifacts and MUST NOT replace or overwrite those artifacts.
- **FR-007**: Missing, degraded, skipped, unsafe, or escalated evidence states MUST be represented with bounded operator-visible reasons.
- **FR-008**: Artifact presentation for remediation evidence MUST obey normal preview, redaction, authorization, and safe-reference rules.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-623` and the original Jira preset brief for traceability.

### Key Entities

- **Remediation Evidence Set**: The complete operator-facing evidence trail for one remediation run, including only artifacts applicable to that run path.
- **Decision Log Entry**: A bounded record of one repair candidate decision, including outcome, rationale, and references to related action, verification, prevention, or no-PR evidence.
- **Remediation Summary**: The stable run-level summary block that exposes target identity, remediation mode, authority, actions, repair/prevention outcomes, resolution, degraded evidence, and escalation state.
- **Audit Event**: A compact queryable control-plane record for a side-effecting remediation decision.
- **Target Annotation**: Supplemental target-side evidence that links a target-managed session or workload mutation back to the remediation decision without replacing target-native records.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of representative remediation run paths expose every applicable remediation evidence artifact and clearly identify non-applicable artifacts as such.
- **SC-002**: 100% of side-effecting remediation action decisions in representative runs produce a queryable bounded audit record.
- **SC-003**: 100% of completed representative remediation runs include stable summary fields for target identity, mode, authority, resolution, degraded evidence, and escalation state.
- **SC-004**: 100% of attempted, skipped, denied, or escalated repair candidates in representative runs include bounded rationale and evidence references where applicable.
- **SC-005**: Artifact preview and metadata checks reveal zero raw storage URLs, raw local paths, secrets, or unredacted sensitive values across representative remediation evidence.
- **SC-006**: Traceability review confirms `MM-623`, the original Jira preset brief, and source coverage IDs DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-028 are preserved in MoonSpec artifacts and final evidence.
