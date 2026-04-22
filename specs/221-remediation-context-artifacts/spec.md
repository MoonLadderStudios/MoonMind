# Feature Specification: Remediation Context Artifacts

**Feature Branch**: `221-remediation-context-artifacts`
**Created**: 2026-04-21
**Status**: Draft
**Input**: Jira Orchestrate for MM-432 using `docs/tmp/jira-orchestration-inputs/MM-432-moonspec-orchestration-input.md` as the canonical MoonSpec orchestration input.

Source story: STORY-002.
Source summary: Build bounded remediation context artifacts.
Source Jira issue: MM-432.
Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-432-moonspec-orchestration-input.md`.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside the breakdown task.

## Original Preset Brief

```text
# MM-432 MoonSpec Orchestration Input

## Source

- Jira issue: MM-432
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Build bounded remediation context artifacts
- Labels: `moonmind-workflow-mm-a59f3b1d-da4d-4600-86a8-1d582ee67fe8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-432 from MM project
Summary: Build bounded remediation context artifacts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-432 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-432: Build bounded remediation context artifacts

Source Reference
- Source Document: docs/Tasks/TaskRemediation.md
- Source Title: Task Remediation
- Source Sections:
  - 9. Evidence and context model
  - 14.1 Required remediation artifacts
  - 16. Failure modes and edge cases
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-011
  - DESIGN-REQ-019
  - DESIGN-REQ-022
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a remediation task, I receive a bounded artifact-backed context bundle for the target execution so diagnosis starts from durable evidence instead of unbounded logs or workflow history.

Acceptance Criteria
- A remediation task produces `reports/remediation_context.json` with `artifact_type` `remediation.context` before diagnosis begins.
- The artifact includes target `workflowId`/`runId`, selected steps, observability refs, bounded summaries, diagnosis hints, policy snapshots, lock policy snapshot, and live-follow cursor state when applicable.
- Large logs, diagnostics, provider snapshots, and evidence bodies remain behind artifact refs or observability refs rather than being embedded unbounded in the context artifact.
- Missing artifact refs, unavailable diagnostics, and historical merged-log-only runs produce explicit degraded evidence metadata without deadlocking the remediation task.
- The context builder never places presigned URLs, raw storage keys, absolute local filesystem paths, raw secrets, or secret-bearing config bundles in durable context.

Requirements
- Evidence access is artifact-first and bounded.
- The context bundle is the stable entrypoint for the remediation runtime.
- Partial evidence is represented as a bounded degradation, not an infinite wait.
- Artifact presentation and redaction contracts apply to context metadata and bodies.

Implementation Notes
- Preserve MM-432 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation evidence, context bundle shape, required remediation artifacts, and degradation behavior.
- Scope implementation to building the bounded `reports/remediation_context.json` artifact before diagnosis begins.
- Keep full logs, diagnostics, provider snapshots, evidence bodies, presigned URLs, raw storage keys, absolute local paths, and secret-bearing config bundles out of the durable context artifact.
- Represent missing artifact refs, unavailable diagnostics, historical merged-log-only evidence, and unavailable live follow as explicit bounded degradation metadata.
- Include target identity, selected steps, observability refs, compact diagnosis hints, action and approval policy snapshots, lock policy snapshot, and live-follow cursor state when applicable.
- Keep the context artifact as the stable entrypoint for remediation runtime evidence; richer evidence should remain reachable through typed artifact or observability refs.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-432 blocks MM-431, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-432 is blocked by MM-433, whose embedded status is Backlog.
```

## User Story - Build Bounded Remediation Context

**Summary**: As a remediation task operator, I want MoonMind to generate a bounded remediation context artifact for the target execution so the remediator starts from stable evidence refs instead of unbounded logs or raw storage access.

**Goal**: A created remediation execution can request a context bundle for its persisted target link and receive a MoonMind-owned `reports/remediation_context.json` artifact linked to the remediation execution.

**Independent Test**: Create a target execution and a remediation execution, build the remediation context, then verify the artifact is complete, linked to the remediation execution, records the pinned target identity, preserves selected evidence refs, and excludes raw log bodies, storage URLs, local paths, or secrets.

**Acceptance Scenarios**:

1. **Given** a remediation execution with a persisted target link, **When** the context builder runs, **Then** it creates a complete `remediation.context` artifact linked to the remediation execution.
2. **Given** the remediation request selected steps, task run IDs, evidence policy, approval policy, and lock policy, **When** the context artifact is generated, **Then** the artifact includes compact normalized copies of those selectors and policies.
3. **Given** the target execution has artifact refs and execution metadata, **When** the context artifact is generated, **Then** the artifact includes refs and bounded metadata without embedding artifact contents or raw logs.
4. **Given** a workflow ID that is not a persisted remediation execution, **When** context generation is requested, **Then** the builder fails before writing an artifact.

### Edge Cases

- Evidence policy `tailLines` must be clamped to a bounded maximum.
- Excess `taskRunIds` must be bounded so one request cannot create an unbounded artifact.
- Missing optional target artifact refs must degrade to empty evidence lists rather than blocking context generation.
- Context generation must not expose presigned URLs, storage keys, absolute local filesystem paths, or raw secret-like fields.

## Assumptions

- This story builds the artifact generation slice only. Remediation action execution, locks, live-follow streaming, and evidence read tools remain later stories.
- A persisted remediation link from MM-431 already exists before context generation runs.
- The artifact link plus remediation link context reference are sufficient for the first operator and runtime lookup surfaces.

## Source Design Requirements

- **DESIGN-REQ-006** (`docs/Tasks/TaskRemediation.md` section 9.2): A Remediation Context Builder must create `reports/remediation_context.json` with artifact type `remediation.context` as the remediation task's stable evidence entrypoint. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-011** (`docs/Tasks/TaskRemediation.md` sections 9.2 and 9.3): The context artifact must contain target identity, selected steps, observability refs, bounded summaries, diagnosis hints, policy snapshots, lock policy snapshot, and live-follow cursor state when available. Scope: in scope, mapped to FR-003 and FR-004.
- **DESIGN-REQ-019** (`docs/Tasks/TaskRemediation.md` sections 9.4, 10.4, 10.5, and 14.1): Context artifacts and required remediation artifacts must stay bounded and must not include raw secrets, presigned URLs, storage keys, absolute local paths, or unbounded log bodies. Scope: in scope, mapped to FR-005 and FR-006.
- **DESIGN-REQ-022** (`docs/Tasks/TaskRemediation.md` section 14.1): The remediation context artifact must obey the normal artifact presentation contract by storing safe metadata, artifact refs rather than URLs, correct preview/redaction handling, and no secrets in metadata or bodies. Scope: in scope, mapped to FR-002, FR-004, and FR-006.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` sections 16.1 through 16.5): Missing target visibility, partial artifact refs, historical merged-log-only evidence, and unavailable live follow must produce fail-fast validation or explicit bounded degraded evidence rather than deadlocking context generation. Scope: in scope, mapped to FR-001, FR-004, and FR-007.
- **DESIGN-REQ-024** (`docs/Tasks/TaskRemediation.md` sections 9.5 through 11 and 16.6 through 16.11): Remediation evidence read tools, lock handling, typed action execution, and remediation failure summaries are later-stage capabilities. Scope: out of scope for this artifact-generation slice.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a service boundary that generates a remediation context artifact only for executions with a persisted remediation link.
- **FR-002**: The generated artifact MUST be stored as a complete Temporal artifact, linked to the remediation execution with link type `remediation.context`, and recorded on the remediation link.
- **FR-003**: The artifact payload MUST include schema version, remediation workflow identity, generation time, pinned target workflow/run identity, target title/state/close status, selected step selectors, and requested task run IDs.
- **FR-004**: The artifact payload MUST include compact evidence refs and policy snapshots for evidence, authority, action, approval, lock, and live-follow mode.
- **FR-005**: The builder MUST bound user-controlled evidence hints, including clamping `tailLines` and limiting task run IDs.
- **FR-006**: The artifact payload MUST NOT include raw log bodies, artifact byte contents, storage backend keys, presigned URLs, absolute local filesystem paths, raw credential fields, or unbounded diagnostics.
- **FR-007**: Requests for non-remediation workflow IDs MUST fail without writing a context artifact.

### Key Entities

- **Remediation Context Artifact**: A JSON artifact linked to the remediation execution that acts as the stable evidence entrypoint.
- **Remediation Link Context Reference**: A nullable reference from the remediation relationship to the generated context artifact.
- **Bounded Evidence Ref**: A compact reference to an artifact, task run, or execution evidence surface without raw content or storage access details.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove context generation creates and links exactly one complete `remediation.context` artifact for a remediation execution.
- **SC-002**: Unit tests prove the payload includes pinned target identity, selectors, bounded task run IDs, evidence refs, and policies.
- **SC-003**: Unit tests prove the payload omits raw storage keys, local paths, URLs, and secret-like raw fields.
- **SC-004**: Unit tests prove non-remediation workflow IDs fail before artifact creation.
