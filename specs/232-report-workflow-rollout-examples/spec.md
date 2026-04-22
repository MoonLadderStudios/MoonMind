# Feature Specification: Report Workflow Rollout and Examples

**Feature Branch**: `232-report-workflow-rollout-examples`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-464 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-464-moonspec-orchestration-input.md`

## Original Jira Preset Brief

Jira issue: MM-464 from MM project
Summary: Report Workflow Rollout and Examples
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-464 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-464: Report Workflow Rollout and Examples

Short Name
report-workflow-rollout-examples

Source Reference
- Source document: `docs/Artifacts/ReportArtifacts.md`
- Source title: Report Artifacts
- Source sections: 17. Example workflow mappings, 18. Suggested API/UI extensions, 19. Migration guidance, 20. Open questions
- Coverage IDs: DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022

User Story
As a MoonMind maintainer, I can migrate report-producing workflow families incrementally to the report artifact contract with documented examples and graceful fallback for existing generic outputs.

Acceptance Criteria
- Unit-test, coverage, pentest/security, and benchmark report examples each map to `report.primary` and appropriate summary, structured, evidence, runtime, and diagnostic artifacts.
- New report-producing workflows prefer `report.*` semantics while existing generic `output.primary` flows continue to operate.
- The UI degrades gracefully when an execution has only generic output artifacts and no `report.primary` artifact.
- Migration docs or examples distinguish curated reports, evidence, runtime stdout/stderr, and diagnostics for each workflow family.
- Convenience report fields or endpoints, if introduced during rollout, remain projections over the normal artifact APIs.

Requirements
- Document and validate report mappings for unit-test, coverage, pentest/security, and benchmark workflows.
- Support incremental rollout phases: metadata conventions, explicit `report.*` link types and UI surfacing, compact report bundle contract, and optional projections, filters, retention, and pinning.
- Keep existing generic outputs available during migration.
- Preserve clear separation between curated reports and observability artifacts in examples and tests.

Relevant Implementation Notes
- Use the existing artifact APIs as the baseline for the first rollout; report-aware convenience surfaces must remain projections over normal artifacts.
- Represent report-producing workflow examples with explicit artifact families, including `report.primary`, `report.summary`, `report.structured`, `report.evidence`, `runtime.stdout`, `runtime.stderr`, and `runtime.diagnostics` where applicable.
- Include example mappings for unit test reports, coverage reports, pentest or security reports, and benchmark or evaluation reports.
- Keep report metadata bounded to display and classification fields such as `artifact_type`, `producer`, `subject`, `finding_counts`, `severity_counts`, and `sensitivity` where relevant.
- Preserve fallback behavior for executions that only have generic output artifacts and no `report.primary` artifact.
- Treat suggested execution detail fields and any report projection endpoint as optional read models over the underlying artifact references, not as a second storage model.
- Preserve MM-464 and coverage IDs DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, and DESIGN-REQ-022 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- Requiring a flag-day migration for existing report-producing workflows.
- Replacing generic `output.primary` behavior for existing workflows during rollout.
- Creating a second report storage model or making report convenience fields authoritative over artifact records.
- Forcing immediate implementation of every optional API/UI extension or open question from the source document.
- Collapsing curated reports, evidence, runtime stdout/stderr, diagnostics, and other observability artifacts into one undifferentiated output.

Validation
- Verify unit-test, coverage, pentest/security, and benchmark examples map to `report.primary` and the appropriate related report, runtime, and diagnostic artifacts.
- Verify new report-producing workflow guidance prefers explicit `report.*` semantics while existing generic `output.primary` flows remain valid.
- Verify UI or documentation guidance describes graceful fallback when an execution has generic outputs but no `report.primary` artifact.
- Verify migration guidance distinguishes curated reports, evidence, runtime stdout/stderr, and diagnostics for each workflow family.
- Verify any introduced convenience fields or report endpoint behavior is documented and implemented as a projection over normal artifact APIs.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable maintainer story and does not require processing multiple specs.
- Selected mode: Runtime.
- Source design: `docs/Artifacts/ReportArtifacts.md` is treated as runtime source requirements because the Jira brief points at implementation behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-464 were found under `specs/`; specification is the first incomplete stage.

## User Story - Validate Report Workflow Rollout Mappings

**Summary**: As a MoonMind maintainer, I want report-producing workflow families to have executable rollout mappings for report, evidence, runtime, and diagnostic artifacts so migrations can prefer report semantics while generic output fallback remains safe.

**Goal**: Runtime code can identify and validate the expected artifact classes for unit-test, coverage, pentest/security, and benchmark reports without conflating curated reports with observability artifacts or breaking existing generic outputs.

**Independent Test**: Validate each supported workflow family mapping in isolation, then verify a new report-producing workflow must include `report.primary`, evidence/runtime/diagnostic artifacts stay distinct where expected, and an execution with only generic `output.primary` is classified as a legacy fallback rather than a canonical report.

**Acceptance Scenarios**:

1. **Given** a unit-test report mapping is requested, **When** the runtime returns the example mapping, **Then** it includes `report.primary`, `report.summary`, `report.structured`, optional `report.evidence`, `runtime.stdout`, `runtime.stderr`, and `runtime.diagnostics` with unit-test metadata guidance.
2. **Given** coverage, pentest/security, and benchmark mappings are requested, **When** the runtime returns each mapping, **Then** each maps curated report artifacts separately from evidence and observability artifacts.
3. **Given** a new report-producing workflow validates its artifact classes, **When** it omits `report.primary` without explicitly using legacy fallback, **Then** validation fails instead of silently treating `output.primary` as a report.
4. **Given** an existing execution has generic output artifacts and no `report.primary`, **When** it is classified for report rollout presentation, **Then** it is identified as a generic fallback with no canonical report.
5. **Given** a convenience report summary is built from a report bundle, **When** refs are present, **Then** the summary contains only projection data over artifact refs and no report body or evidence payload.

### Edge Cases

- An unknown workflow family name is provided.
- A report-producing workflow publishes evidence but omits its primary report.
- Generic `output.primary` exists alongside report evidence but no `report.primary`.
- A convenience summary input contains inline body, raw URL, transcript, or evidence payload fields.
- A workflow family requires no stdout/stderr artifact but still requires diagnostics.

## Assumptions

- Report link types and compact report bundle helpers from MM-460 and MM-461 are available.
- Mission Control report presentation from MM-462 already handles the UI fallback once the runtime reports no canonical `report.primary`.
- The first runtime slice should provide deterministic helper functions and tests, not force all workflow producers to migrate in one change.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | `docs/Artifacts/ReportArtifacts.md` §17 | Unit-test, coverage, pentest/security, and benchmark workflow examples use stable `report.*` link types for curated report artifacts. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-007 | `docs/Artifacts/ReportArtifacts.md` §17 | Examples must distinguish curated reports and evidence from runtime stdout/stderr and diagnostics. | In scope | FR-001, FR-004 |
| DESIGN-REQ-019 | `docs/Artifacts/ReportArtifacts.md` §17.1-§17.4 | Each workflow family has recommended metadata such as artifact type, producer, subject, finding counts, severity counts, or sensitivity. | In scope | FR-002 |
| DESIGN-REQ-020 | `docs/Artifacts/ReportArtifacts.md` §17, §19 | Existing generic outputs continue to operate during incremental rollout and are treated as fallback when no canonical report exists. | In scope | FR-005, FR-006 |
| DESIGN-REQ-021 | `docs/Artifacts/ReportArtifacts.md` §19 | Rollout phases progress from metadata conventions, to explicit report links and UI surfacing, to compact bundles, then optional projections/filters/retention/pinning. | In scope | FR-007 |
| DESIGN-REQ-022 | `docs/Artifacts/ReportArtifacts.md` §18 | Convenience report fields or endpoints are projections over normal artifact APIs and refs, not separate storage or authoritative report bodies. | In scope | FR-008 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose deterministic report workflow example mappings for unit-test, coverage, pentest/security, and benchmark report-producing workflow families.
- **FR-002**: Each workflow example mapping MUST identify expected report artifact classes, observability artifact classes, and recommended bounded metadata keys for the workflow family.
- **FR-003**: New report-producing workflow validation MUST require `report.primary` unless the caller explicitly treats the execution as a legacy generic-output fallback.
- **FR-004**: Workflow mapping validation MUST preserve separation between curated report artifacts, report evidence artifacts, runtime stdout/stderr, and runtime diagnostics.
- **FR-005**: Existing generic `output.primary`, `output.summary`, and `output.agent_result` artifacts MUST remain valid generic output classes during rollout.
- **FR-006**: Report rollout classification MUST identify executions with generic output artifacts and no `report.primary` as generic fallback rather than canonical report executions.
- **FR-007**: The system MUST expose the ordered incremental rollout phases for report-producing workflows.
- **FR-008**: Convenience report summaries derived during rollout MUST contain only projection data over normal artifact refs and bounded metadata, not report bodies, evidence payloads, logs, screenshots, transcripts, raw URLs, or separate storage identifiers.
- **FR-009**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-464.

### Key Entities

- **Report Workflow Mapping**: A deterministic example contract for one report-producing workflow family, including report link types, observability link types, and recommended metadata.
- **Report Rollout Classification**: A compact decision describing whether a set of artifact link types contains a canonical report, a generic fallback, or an invalid report-producing output.
- **Report Projection Summary**: A convenience read model derived from compact artifact refs and bounded metadata over normal artifacts.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of supported workflow family mappings include `report.primary` and the expected report/evidence/runtime/diagnostic classes from the Jira brief.
- **SC-002**: Validation tests reject report-producing workflow outputs that omit `report.primary` unless legacy fallback is explicitly requested.
- **SC-003**: Classification tests identify generic `output.primary`-only executions as fallback with no canonical report.
- **SC-004**: Projection summary tests reject inline report bodies, evidence payloads, logs, screenshots, transcripts, raw URLs, and unsupported storage identifiers.
- **SC-005**: MM-464 appears in the spec, plan, tasks, verification evidence, and publish metadata for traceability.
