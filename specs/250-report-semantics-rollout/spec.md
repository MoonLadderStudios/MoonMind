# Feature Specification: Report Semantics Rollout

**Feature Branch**: `250-report-semantics-rollout`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-497 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `spec.md` (Input).
Classification: single-story runtime feature request.
Resume decision: no existing feature directory in `specs/` matched MM-497, so `Specify` is the first incomplete stage.
Breakdown decision: `moonspec-breakdown` was not run because the MM-497 Jira preset brief already defines one independently testable runtime story.

## Original Preset Brief

```text
# MM-497 MoonSpec Orchestration Input

## Source

- Jira issue: MM-497
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Roll out report semantics without flag-day migration
- Labels: `moonmind-workflow-mm-1f7a8be1-a624-482c-bcb5-4a86e5ab4b1b`
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-497`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-497 from MM project
Summary: Roll out report semantics without flag-day migration
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-497 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-497: Roll out report semantics without flag-day migration

Source Reference
Source Document: docs/Artifacts/ReportArtifacts.md
Source Title: Report Artifacts
Source Sections:
- 2 Scope / Non-goals
- 5 Non-goals
- 17 Example workflow mappings
- 19 Migration guidance
- 20 Open questions
- 21 Bottom line
Coverage IDs:
- DESIGN-REQ-021
- DESIGN-REQ-023
- DESIGN-REQ-024
As a MoonMind maintainer, I want report artifact semantics to roll out incrementally with documented non-goals and producer examples so existing generic outputs continue working while new report workflows adopt report.* conventions.

## Normalized Jira Detail

Acceptance criteria:
- Existing generic output.primary workflows continue to function without being reclassified as reports.
- New report workflows prefer report.* link types and report metadata conventions.
- Migration can proceed through metadata conventions, explicit link types/UI surfacing, compact bundle results, and later projections/filters/retention/pinning.
- PDF rendering engines, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing remain out of scope unless separately specified.
- Examples document unit-test, coverage, pentest/security, and benchmark report mappings.
- Unresolved product choices around report_type enums, auto-pinning, projection endpoint timing, export semantics, evidence grouping, and multi-step task projections are tracked as clarification points for later stories.

Requirements:
- Support incremental report rollout with generic-output compatibility.
- Document representative workflow mappings and explicit non-goals.
- Keep migration notes under local-only handoffs when implementation tracking is needed.
```

## User Story - Roll Out Report Semantics Incrementally

**Summary**: As a MoonMind maintainer, I want report semantics to roll out incrementally so existing generic outputs continue working while new report-producing workflows adopt explicit report conventions.

**Goal**: MoonMind preserves generic output behavior for existing workflows, enables new workflows to opt into canonical `report.*` semantics explicitly, and keeps migration boundaries, examples, and deferred product choices visible instead of requiring a flag-day cutover.

**Independent Test**: Exercise one existing generic-output workflow and one report-producing workflow, then verify the generic workflow remains non-report output by default, the report workflow uses explicit report conventions, migration-safe semantics remain bounded, and deferred product choices are preserved rather than being silently implied.

**Acceptance Scenarios**:

1. **Given** an existing workflow publishes generic `output.primary` artifacts, **When** the rollout semantics are applied, **Then** that workflow continues to function without being reclassified as a report producer by default.
2. **Given** a new workflow intends to publish a report, **When** it opts into canonical report behavior, **Then** it uses explicit `report.*` link types and report metadata conventions rather than generic output semantics.
3. **Given** report semantics are being introduced incrementally, **When** related follow-on work lands over time, **Then** migration can proceed without a flag-day cutover across metadata conventions, explicit link types and UI surfacing, compact bundle results, and later projection, filter, retention, and pinning work.
4. **Given** report semantics rollout is active, **When** downstream implementations are planned, **Then** PDF rendering engines, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing remain out of scope unless separately specified.
5. **Given** teams need examples of the target semantics, **When** they reference the supported workflow mappings, **Then** representative unit-test, coverage, pentest/security, and benchmark report mappings are available under the shared report-semantics model.
6. **Given** some product decisions remain unresolved, **When** planning and verification artifacts are reviewed, **Then** open questions around report type enums, auto-pinning, projection timing, export semantics, evidence grouping, and multi-step task projections are preserved as explicit follow-up items rather than being silently decided in this story.

### Edge Cases

- A workflow emits both generic output artifacts and report artifacts during the same execution.
- A producer uses report-like metadata on a generic output artifact without adopting explicit `report.*` link types.
- A rollout step tries to require a PDF renderer, provider-specific prompt contract, or full-text indexing before canonical report semantics can be used.
- Later stories add projections, retention, or pinning behavior without preserving the migration-safe distinction between existing generic outputs and explicit report workflows.
- Consumers assume any artifact with summary-like metadata is automatically the canonical report even when explicit report semantics were not used.

## Assumptions

- Existing generic-output workflows must remain valid during the rollout period because other report stories already depend on incremental adoption rather than a flag-day replacement.
- Representative producer mappings can be validated through MoonMind workflow families without forcing all producers into one universal report findings schema.
- Open product questions listed in the Jira brief are intentionally deferred to later stories and should remain visible rather than resolved implicitly here.

## Dependencies

- Existing generic-output workflows remain the compatibility baseline that this story must preserve throughout the rollout.
- Report-producing workflows depend on explicit canonical `report.*` semantics being distinct from generic output behavior.
- Later report stories own deferred capabilities such as projection timing, auto-pinning, export semantics, and related follow-up decisions preserved by this story.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-021 | `docs/Artifacts/ReportArtifacts.md` §2, §17, §21 | Existing generic-output workflows must remain valid while newer report workflows adopt explicit canonical report semantics. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-023 | `docs/Artifacts/ReportArtifacts.md` §5, §19, §20 | Report semantics rollout must keep explicit non-goals and deferred migration choices visible so the system does not imply unsupported report capabilities during the incremental rollout. | In scope | FR-003, FR-004, FR-006 |
| DESIGN-REQ-024 | `docs/Artifacts/ReportArtifacts.md` §17, §20, §21 | The system must preserve representative workflow mappings and explicit follow-up clarifications so future stories can extend report behavior without breaking the incremental rollout boundary. | In scope | FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve existing generic `output.primary` workflow behavior and MUST NOT automatically reclassify those outputs as reports during the incremental rollout.
- **FR-002**: The system MUST require new report-producing workflows to opt into canonical report behavior through explicit `report.*` link types and report metadata conventions.
- **FR-003**: The system MUST support an incremental rollout path in which metadata conventions, explicit link types or UI surfacing, compact bundle results, and later projection, filter, retention, or pinning work can ship without a flag-day migration of existing generic outputs.
- **FR-004**: The system MUST keep PDF rendering engines, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing out of scope for this story unless a later story specifies them explicitly.
- **FR-005**: The system MUST preserve representative workflow mappings for unit-test, coverage, pentest or security, and benchmark-style reports so producers can align to the shared report-semantics model.
- **FR-006**: The system MUST preserve unresolved choices around report type enums, auto-pinning, projection timing, export semantics, evidence grouping, and multi-step task projections as explicit follow-up clarifications rather than silently deciding them in this story.
- **FR-007**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this story MUST preserve Jira issue key MM-497.

### Key Entities

- **Generic Output Workflow**: A workflow that publishes existing non-report `output.primary` style artifacts and must remain valid during the rollout.
- **Report-Producing Workflow**: A workflow that explicitly opts into canonical report behavior through `report.*` semantics.
- **Incremental Rollout Boundary**: The rule set that lets report semantics expand over multiple stories without forcing a one-time migration of existing generic outputs.
- **Representative Workflow Mapping**: A documented and testable example of how one workflow family adopts the shared report-semantics model.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves existing generic-output workflows continue to function without being reclassified as reports by default.
- **SC-002**: Validation proves new report-producing workflows use explicit `report.*` link types and report metadata conventions when adopting canonical report behavior.
- **SC-003**: Validation proves incremental rollout can proceed without a flag-day migration while preserving the distinction between generic outputs and report outputs.
- **SC-004**: Validation proves out-of-scope capabilities for this story are not required for the rollout semantics to work.
- **SC-005**: Validation proves representative workflow mappings exist for unit-test, coverage, pentest or security, and benchmark report producers.
- **SC-006**: Planning and verification artifacts preserve the open follow-up decisions listed in MM-497 instead of silently deciding them.
- **SC-007**: Traceability review confirms MM-497 and DESIGN-REQ-021, DESIGN-REQ-023, and DESIGN-REQ-024 remain preserved in MoonSpec artifacts and downstream implementation evidence.
