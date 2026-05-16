# Feature Specification: Preserve Slash Command Fidelity Across Edit, Rerun, Details, and Audit

**Feature Branch**: `357-preserve-slash-command-fidelity`
**Created**: 2026-05-15
**Status**: Draft
**Input**:

```text
For a single-story Jira preset brief, run moonspec-specify unless an active spec.md already passes the specify gate.
For a broad technical or declarative design, run moonspec-breakdown first, then select the recommended first generated spec unless the issue brief explicitly requires processing all specs.
Preserve Jira issue MM-687 and the original preset brief in spec.md so final verification can compare against them.

Canonical Jira preset brief:

# MM-687 MoonSpec Orchestration Input

## Source

- Jira issue: MM-687
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Preserve slash command fidelity across edit, rerun, task details, and audit surfaces
- Priority: Medium
- Labels: `moonmind-workflow-mm-1c30567c-221e-4dc1-bc74-d1248e750656`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, `Test plan`, and `Source` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-687 from MM project
Summary: Preserve slash command fidelity across edit, rerun, task details, and audit surfaces
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-687 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-687: Preserve slash command fidelity across edit, rerun, task details, and audit surfaces

Source Reference
Source Document: docs/Steps/SlashCommands.md
Source Title: Runtime Slash Commands on Create Task
Source Sections:
- Create Page Behavior
- Edit mode
- Rerun mode
- Audit and Observability
- Testing Strategy
- Acceptance Criteria

Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-018

As an operator reviewing or rerunning previous work, I want MoonMind to show the original authored instructions and the runtime command interpretation used at submission, so historical tasks remain auditable even as runtime capabilities or hints evolve.

Acceptance Criteria
- Edit mode restores authored instructions and runtimeCommand metadata from the task input snapshot when present.
- If historical metadata is absent, edit mode may re-detect only for preview and must not alter the historical raw instruction value silently.
- Exact rerun preserves original authored instructions, runtimeCommand metadata, runtimeCapabilityVersion, and hintCatalogVersion.
- Edit-for-rerun may display recomputed warnings without mutating the original source run.
- Task details show both original instructions and runtime command interpretation including command, runtime, render mode, and status when available.
- Audit events record runtime_command.detected, runtime_command.rendered, and runtime_command.passthrough details without secrets.

Requirements
- Store enough command metadata to explain historical recognition mode and catalog versions.
- Use snapshot metadata as the source of truth for edit, rerun, task details, and audit views.
- Emit observability events for detected, rendered, and opaque pass-through command cases.
- Display original authored instructions alongside interpretation instead of replacing one with the other.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

Preserved source Jira preset brief: `MM-687` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-687` and local handoff `/work/agent_jobs/mm:e1afde4a-fc92-48d9-811d-6ca6df9c1b32/artifacts/moonspec/MM-687-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-687` slash command fidelity brief was found under `specs/`; `Specify` is the first incomplete stage.

## User Story - Audit Historical Slash Command Meaning

**Summary**: As an operator reviewing, editing, or rerunning previous work, I want MoonMind to preserve and display both the original authored instructions and the runtime command interpretation captured at submission so historical tasks remain auditable even when runtime capabilities or hints later change.

**Goal**: Operators can edit, rerun, inspect, and audit slash-command tasks without losing the original authored text, the submitted runtime command metadata, or the catalog version context that explains how the command was understood.

**Independent Test**: Can be fully tested by creating a slash-command task, saving its submitted task snapshot, then validating edit mode, exact rerun, edit-for-rerun, task details, and audit views against the preserved snapshot and command metadata after runtime capability or hint catalog assumptions change.

**Acceptance Scenarios**:

1. **Given** a historical task input snapshot contains authored slash-leading instructions and runtime command metadata, **When** an operator opens the task in edit mode, **Then** the authored instructions and runtime command metadata are restored from the snapshot.
2. **Given** a historical task input snapshot has authored instructions but no runtime command metadata, **When** an operator opens the task in edit mode, **Then** MoonMind may show a fresh preview or warning but must not silently replace or mutate the historical raw instruction value.
3. **Given** an operator starts an exact rerun of a slash-command task, **When** the rerun is prepared, **Then** the original authored instructions, runtime command metadata, runtime capability version, and hint catalog version are preserved from the source run.
4. **Given** an operator chooses edit-for-rerun, **When** runtime capability or hint data has changed since the original run, **Then** MoonMind may recompute warnings for the editable copy without mutating the source run's preserved instructions or metadata.
5. **Given** an operator views task details for a slash-command task, **When** command interpretation metadata is available, **Then** the task detail view shows original instructions alongside command, runtime, render mode, and status information.
6. **Given** slash-command detection, rendering, or opaque pass-through occurs, **When** audit events are recorded, **Then** the events include command interpretation details needed for audit while excluding secrets.

### Edge Cases

- Historical task snapshots created before runtime command metadata existed can still be opened for edit and rerun without changing the original instruction text.
- Runtime capability or hint catalog versions have changed since the source run; exact rerun keeps the original versions while edit-for-rerun may surface current warnings.
- Command metadata is incomplete or malformed; operator surfaces identify the missing interpretation without inventing a historical command result.
- Task details load for a task that contains literal escaped slash text; the view distinguishes literal authored text from a detected runtime command.
- Audit data exists for a pass-through unknown command; the event communicates opaque pass-through status without treating missing hints as a failure.
- Any displayed or recorded command text, arguments, bodies, or diagnostics may contain sensitive-looking user text; audit and detail surfaces must not expose secrets beyond the authorized authored content.

## Assumptions

- The authoritative task input snapshot is the durable source of truth for the original authored instructions and submitted runtime command metadata.
- Existing slash-command normalization, preview, and runtime rendering stories provide command metadata values that this story preserves and displays.
- Exact rerun means preserving source-run inputs; edit-for-rerun means creating an editable copy that may show current warnings without changing source-run evidence.

## Source Design Requirements

| Requirement ID | Source Citation | Requirement Summary | Scope | Mapped Requirement |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-002 | `docs/Steps/SlashCommands.md` lines 160-197, RuntimeCommandInvocation metadata | Runtime command metadata must include recognition mode, runtime capability version, hint catalog version, and detection phase so historical interpretation can be explained. | In scope | FR-001, FR-002, FR-006 |
| DESIGN-REQ-003 | `docs/Steps/SlashCommands.md` lines 540-547, Edit mode | Edit mode must restore authored instructions and runtime command metadata from the task input snapshot when present, and preview-only re-detection must not mutate historical text. | In scope | FR-003, FR-004 |
| DESIGN-REQ-014 | `docs/Steps/SlashCommands.md` lines 549-553, Rerun mode | Rerun must preserve original authored instructions and command metadata, including capability and hint catalog versions used at submit time. | In scope | FR-005, FR-006 |
| DESIGN-REQ-015 | `docs/Steps/SlashCommands.md` lines 718-777, Audit and Observability | Audit and task detail surfaces must show original authored instructions and runtime command interpretation, including command, runtime, render mode, and pass-through status. | In scope | FR-007, FR-008, FR-009 |
| DESIGN-REQ-018 | `docs/Steps/SlashCommands.md` lines 779-790 and 917-923, Security Requirements and edit/rerun tests | Authored command text must remain auditable, backend validation remains authoritative, command text is untrusted, secrets are not exposed, and edit/rerun/detail behavior has validation coverage. | In scope | FR-010, FR-011, FR-012 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST preserve submitted runtime command metadata that explains recognition mode, runtime capability version, hint catalog version, source path, command, arguments, instruction body, and detection phase when that metadata is present in the task input snapshot.
- **FR-002**: System MUST preserve original authored instructions separately from runtime command interpretation so one cannot silently replace the other.
- **FR-003**: Edit mode MUST restore authored instructions from the task input snapshot.
- **FR-004**: Edit mode MUST restore runtime command metadata from the task input snapshot when present, and any re-detection for historical tasks without metadata MUST be preview-only.
- **FR-005**: Exact rerun MUST preserve original authored instructions, runtime command metadata, runtime capability version, and hint catalog version from the source run.
- **FR-006**: When exact rerun uses preserved metadata whose capability or hint version differs from current catalog data, System MUST keep the original metadata and make any changed-assumption warning distinguishable from source-run evidence.
- **FR-007**: Edit-for-rerun MAY recompute current warnings or preview interpretation, but it MUST NOT mutate the original source run's authored instructions or runtime command metadata.
- **FR-008**: Task details MUST show original authored instructions for slash-command tasks.
- **FR-009**: Task details MUST show runtime command interpretation when available, including command, runtime, render mode, and status.
- **FR-010**: Audit events for detected, rendered, and opaque pass-through command cases MUST include enough non-secret interpretation details for an operator to understand what happened.
- **FR-011**: Audit and detail surfaces MUST treat command names, arguments, instruction bodies, and metadata as untrusted authored content and MUST NOT expose secrets in diagnostics or event output.
- **FR-012**: Validation coverage MUST prove edit mode, exact rerun, edit-for-rerun, task details, and audit events preserve historical authored instructions and command interpretation according to this spec.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-687` and the original Jira preset brief.

### Key Entities

- **Task Input Snapshot**: The preserved source of original authored instructions and submitted runtime command metadata for a task or run.
- **Runtime Command Interpretation**: The command metadata captured at submission or render time, including command identity, runtime, recognition mode, render mode, status, source path, and catalog versions.
- **Exact Rerun**: A rerun path that preserves source-run instructions and runtime command metadata without applying current preview changes to the source evidence.
- **Edit-for-Rerun**: An editable rerun path that may show current preview warnings while keeping the original source run immutable.
- **Audit Event**: A recorded operator-observable event for command detection, rendering, or pass-through behavior that excludes secrets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested edit-mode loads for snapshots containing runtime command metadata restore both authored instructions and command metadata from the snapshot.
- **SC-002**: 100% of tested edit-mode loads for historical snapshots without command metadata leave the historical raw instruction value unchanged.
- **SC-003**: 100% of tested exact reruns preserve original authored instructions, runtime command metadata, runtime capability version, and hint catalog version from the source run.
- **SC-004**: 100% of tested edit-for-rerun flows leave source-run authored instructions and metadata unchanged while allowing current warnings on the editable copy.
- **SC-005**: 100% of tested task detail views for slash-command tasks display original authored instructions and, when available, command interpretation including command, runtime, render mode, and status.
- **SC-006**: 100% of tested detected, rendered, and opaque pass-through audit events include non-secret command interpretation details and exclude secret values.
- **SC-007**: Traceability review confirms `MM-687`, the original Jira preset brief, and all in-scope source design requirements remain present in MoonSpec artifacts and final verification evidence.
