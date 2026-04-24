# Feature Specification: Publish Durable DooD Observability Outputs

**Feature Branch**: `253-publish-dood-observability`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-504 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `spec.md` (Input).
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched MM-504 under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-504 MoonSpec Orchestration Input

## Source

- Jira issue: MM-504
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish durable DooD artifacts, audit metadata, and observability outputs
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-504 from MM project
Summary: Publish durable DooD artifacts, audit metadata, and observability outputs
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-504 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-504: Publish durable DooD artifacts, audit metadata, and observability outputs

Source Reference
- Source document: `docs/ManagedAgents/DockerOutOfDocker.md`
- Source title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
- Source sections:
  - 13.8 Report publication
  - 14. Artifact, audit, and observability contract
  - 15.6 Secret handling and redaction
- Coverage IDs:
  - DESIGN-REQ-021
  - DESIGN-REQ-022

User Story
As an operator, I can inspect durable logs, diagnostics, summaries, reports, and explicit audit metadata for every Docker-backed workload without relying on daemon state or terminal scrollback.

Acceptance Criteria
- Given any DooD invocation, when it completes or fails, then MoonMind persists invocation summary, stdout, stderr, diagnostics, exit metadata, and declared outputs as durable artifacts.
- Given report publication is requested, when the run completes, then declared primary reports follow the shared artifact publication contract.
- Given unrestricted execution is used, when audit metadata is published, then workflowDockerMode and workloadAccess clearly identify it while dockerHost and secret-looking values remain normalized or redacted.
- Given operators inspect results, then daemon state, container-local history, and terminal scrollback are not required as the source of truth.

Requirements
- Treat artifacts and bounded metadata as authoritative for DooD observability.
- Preserve consistent artifact classes across all launch types.
- Redact secret-looking output and metadata before publication.
- Make unrestricted usage obvious in result metadata and audit surfaces.

Relevant Implementation Notes
- Preserve MM-504 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the source design reference for report publication, artifact and audit contracts, and redaction behavior.
- Ensure durable outputs cover summary, stdout, stderr, diagnostics, exit metadata, and declared artifacts for every DooD invocation.
- Keep published audit metadata explicit about unrestricted execution mode while normalizing or redacting docker host details and secret-looking values.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-504 blocks MM-503, whose embedded status is In Progress.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story; breakdown is reserved for broad technical or declarative designs that contain multiple independently testable stories.
- Selected mode: Runtime.
- Source design: `docs/ManagedAgents/DockerOutOfDocker.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-504 were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for MM-504 because this spec remains isolated to one story.

## User Story - Publish Durable DooD Observability Outputs

**Summary**: As an operator, I want durable artifacts, reports, and audit metadata for every Docker-backed workload so I can inspect execution outcomes without depending on transient daemon state or terminal history.

**Goal**: MoonMind produces consistent durable evidence and bounded audit metadata for every Docker-backed workload execution, publishes reports through the shared artifact contract, redacts secret-looking values, and makes unrestricted execution obvious in operator-visible results.

**Independent Test**: Execute representative Docker-backed workloads across the supported launch types, then verify each run produces durable summary, log, diagnostics, and declared output artifacts; requested reports follow the shared publication contract; audit metadata exposes workload mode and access class without leaking raw secret-looking values; and operators can review results without relying on daemon-local state or scrollback.

**Acceptance Scenarios**:

1. **Given** any Docker-backed workload completes or fails, **When** MoonMind records the outcome, **Then** durable artifacts include invocation summary, stdout, stderr, diagnostics, exit metadata, and declared outputs where available.
2. **Given** a Docker-backed workload declares a primary report, **When** report publication is enabled for the run, **Then** MoonMind publishes that report through the shared artifact publication contract used by other Docker-backed workload surfaces.
3. **Given** a Docker-backed workload runs in an unrestricted execution mode, **When** MoonMind publishes audit metadata, **Then** the metadata makes unrestricted usage obvious and identifies workload mode and access class.
4. **Given** audit metadata or execution output contains docker host details or secret-looking values, **When** MoonMind publishes artifacts and metadata, **Then** those values are normalized or redacted before operator-visible publication.
5. **Given** an operator inspects a completed Docker-backed workload, **When** they review MoonMind’s stored outputs, **Then** durable artifacts and bounded metadata are sufficient without container-local history, daemon state, or terminal scrollback.
6. **Given** different Docker-backed launch types are used, **When** MoonMind publishes their results, **Then** artifact classes and observability expectations remain consistent across those launch types.

### Edge Cases

- A Docker-backed workload fails before normal report publication but still must preserve the minimum durable evidence for diagnosis.
- Secret-looking values appear in stdout, stderr, diagnostics, or metadata fields and must be redacted consistently before publication.
- Unrestricted execution metadata is published without clearly identifying the execution mode or workload access classification.
- One Docker-backed launch type emits different artifact classes or omits bounded audit metadata compared with another launch type.
- Operators attempt to inspect a run after the originating container or daemon state is gone and still need authoritative durable evidence.

## Assumptions

- MM-504 is limited to durable artifact publication, report publication, audit metadata, and observability behavior for Docker-backed workloads, not to changing workload routing or execution-plane ownership already covered by related stories.
- Existing MoonMind artifact storage and retrieval surfaces remain the operator-facing source of truth for reviewing Docker-backed workload results.
- The linked MM-503 relationship is operational context only and does not expand this story beyond durable observability outcomes.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-021 | `docs/ManagedAgents/DockerOutOfDocker.md` §13.8, §14.1-14.5 | Docker-backed workloads must publish durable artifacts, report outputs, and bounded observability records through a shared contract that remains authoritative for operator inspection. | In scope | FR-001, FR-002, FR-003, FR-006 |
| DESIGN-REQ-022 | `docs/ManagedAgents/DockerOutOfDocker.md` §14.3, §15.6 | Docker-backed workload metadata and outputs must make unrestricted execution explicit while normalizing or redacting docker host details and secret-looking values before publication. | In scope | FR-004, FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve durable execution evidence for every Docker-backed workload invocation, including invocation summary, stdout, stderr, diagnostics, exit metadata, and declared outputs where available.
- **FR-002**: The system MUST publish declared primary reports for Docker-backed workloads through the same shared artifact publication contract used by the supported Docker-backed launch types.
- **FR-003**: The system MUST keep durable artifacts and bounded metadata as the authoritative operator-facing record for Docker-backed workload outcomes.
- **FR-004**: The system MUST publish bounded audit metadata for Docker-backed workloads that clearly identifies execution mode and workload access class, including when unrestricted execution is used.
- **FR-005**: The system MUST normalize or redact docker host details and secret-looking values before publishing Docker-backed workload metadata, logs, diagnostics, summaries, or reports.
- **FR-006**: The system MUST preserve consistent artifact classes and observability expectations across the supported Docker-backed launch types.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-504.

### Key Entities

- **Docker-Backed Workload Invocation**: A MoonMind-managed workload execution that runs through one of the supported Docker-backed launch types and produces operator-visible outputs.
- **Durable Observability Evidence**: The persisted summary, logs, diagnostics, exit metadata, declared outputs, and published reports that together form the authoritative execution record.
- **Bounded Audit Metadata**: The operator-visible metadata attached to a Docker-backed workload result that identifies execution mode, workload access, timing, outcome, and publication state without exposing raw secrets.
- **Published Primary Report**: A declared workload output that MoonMind elevates through the shared report publication contract for operator consumption.
- **Artifact Class Contract**: The consistent categorization of runtime and output artifacts that allows operators to inspect different Docker-backed launch types through the same observability expectations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves representative Docker-backed workloads always produce durable summary, log, diagnostics, exit metadata, and declared output evidence for both success and failure paths where available.
- **SC-002**: Validation proves declared primary reports from representative Docker-backed workloads follow the shared report publication contract.
- **SC-003**: Validation proves published audit metadata always identifies workload execution mode and workload access classification, including unrestricted execution.
- **SC-004**: Validation proves docker host details and secret-looking values are normalized or redacted in published metadata and durable outputs.
- **SC-005**: Validation proves operators can inspect completed Docker-backed workload outcomes from MoonMind’s durable artifacts and metadata without requiring daemon-local state or terminal scrollback.
- **SC-006**: Validation proves supported Docker-backed launch types publish consistent artifact classes and observability records.
- **SC-007**: Traceability review confirms MM-504 and DESIGN-REQ-021 through DESIGN-REQ-022 remain preserved in MoonSpec artifacts and downstream verification evidence.
