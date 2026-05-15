# Feature Specification: Normalize Slash-Leading Instructions

**Feature Branch**: `353-normalize-slash-commands`
**Created**: 2026-05-15
**Status**: Draft
**Input**: User description: "# MM-684 MoonSpec Orchestration Input

## Source

- Jira issue: MM-684
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Normalize slash-leading instructions into authoritative runtime command snapshots
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `artifacts/moonspec-inputs/MM-684-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Labels: moonmind-workflow-mm-1c30567c-221e-4dc1-bc74-d1248e750656
- Related Jira issues: is blocked by: MM-685 (Show provider-neutral slash command previews on the Create page)

## Canonical MoonSpec Feature Request

Jira issue: MM-684 from MM project
Summary: Normalize slash-leading instructions into authoritative runtime command snapshots
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-684 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-684: Normalize slash-leading instructions into authoritative runtime command snapshots

Source Reference
Source Document: docs/Steps/SlashCommands.md
Source Title: Runtime Slash Commands on Create Task
Source Sections:
- Purpose
- Problem
- Design Principles
- Goals
- Terminology
- Domain Model
- Task Input Shape
- Detection Rules
- Unknown Command Contract
- Escape Behavior
- Backend Behavior
- Validation policy
- Non-goals
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-010
- DESIGN-REQ-019

As a MoonMind operator submitting a task, I want slash-leading task and step instructions to be preserved as authored text while the backend records structured runtime command metadata, so managed runtimes can later recognize the command without losing audit fidelity.

Acceptance Criteria
- Submitting instructions beginning with /review stores the raw instructions unchanged and a slash_command RuntimeCommandInvocation with command=review, detectionStatus=detected, and a sourcePath matching the instruction field.
- Submitting step instructions beginning with /simplify stores targetStepId and a step sourcePath such as steps[0].instructions.
- Unknown valid commands such as /future-command are accepted as opaque runtime invocations for slash-pass-through runtimes and are never rejected solely because no hint exists.
- Escaped input such as \\/review is stored as escaped_literal metadata and cannot be rendered later as an executable leading slash command by default.
- Malformed ordinary path-like inputs and runtimes without slash pass-through follow configured warning/reject policy without rewriting authored instructions.
- Backend normalization rejects inconsistent or malformed frontend-supplied command metadata and remains authoritative when frontend metadata is missing.

Requirements
- Implement a conservative parser matching the documented grammar and example table while preserving opaque first-line command forms for pass-through runtimes.
- Add RuntimeCommandInvocation and related capability/hint/policy fields to the authoritative task input snapshot shape.
- Compute detectionStatus, hintStatus, recognitionMode, requiresRuntimeRecognition, runtimeCapabilityVersion, hintCatalogVersion, sourcePath, command, args, and instructionBody deterministically.
- Preserve exact user-authored instruction text according to existing task input snapshot rules.
- Treat command hints as enrichment metadata and not as a blocking command allowlist.

## Relevant Implementation Notes

- Source design path: `docs/Steps/SlashCommands.md`.
- Purpose/Problem: preserve user-authored slash-leading task text while deriving structured runtime command metadata, because prepared context can otherwise move `/command` away from the first runtime-visible character.
- Design Principles: preserve user intent exactly, treat slash commands as structured metadata, keep rendering adapter-owned and late, keep support declarative, and preserve edit/rerun fidelity.
- Domain Model: add `RuntimeCommandInvocation` metadata with `kind`, `source`, `sourcePath`, `command`, `rawCommand`, `args`, `instructionBody`, runtime targeting, detection/hint/recognition statuses, capability/catalog versions, and detection phase.
- Task Input Shape: task-level `objective.instructions` and step-level `steps[n].instructions` retain authored text and attach the corresponding `runtimeCommand`; step-level commands include `targetStepId` and source paths such as `steps[0].instructions`.
- Detection Rules: detect only first-character slash-leading instructions; parse known grammar conservatively; accept unknown valid command tokens as opaque runtime invocations for slash-pass-through runtimes; treat escaped, malformed ordinary path-like, or unsupported-runtime cases according to policy.
- Unknown Command Contract: unknown valid slash commands are first-class runtime invocations, are not allowlist failures, remain slash-leading in runtime-visible input, and must not become shell/runtime arguments outside adapter-owned rendering.
- Escape Behavior: `\\/command` records escaped literal metadata and must render as non-executable literal text by default.
- Backend Behavior: backend submit-time normalization is authoritative over frontend preview metadata, validates/canonicalizes supplied metadata, computes command metadata deterministically, stores the authoritative task input snapshot, and rejects inconsistent or malformed frontend-supplied command metadata.
- Validation Policy: default behavior should pass through valid slash commands for supporting runtimes, warn or reject only for unsupported runtimes/malformed command lines per configured policy, and never reject unknown valid commands solely because no hint exists.

## MoonSpec Classification Input

Classify this as a single-story runtime feature request for Create Task slash-command handling: normalize slash-leading task and step instructions into authoritative runtime command snapshots while preserving authored instruction text, audit fidelity, and MM-684 traceability.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"

## User Story - Authoritative Runtime Command Snapshots

**Summary**: As a MoonMind operator submitting a task, I want slash-leading task and step instructions to remain exactly as authored while MoonMind records authoritative structured runtime command metadata, so managed runtimes can recognize commands without losing audit fidelity.

**Goal**: Task submission produces an authoritative task input snapshot that preserves authored instruction text and records deterministic runtime command metadata for task-level and step-level leading slash commands.

**Independent Test**: Submit or normalize task inputs containing slash-leading task instructions, slash-leading step instructions, unknown valid commands, escaped slash text, malformed path-like text, and inconsistent frontend-supplied command metadata; verify the resulting authoritative snapshot contains the expected preserved instructions, command metadata, warnings or rejections, and traceable source paths.

**Acceptance Scenarios**:

1. **Given** task instructions begin with `/review`, **When** the task is submitted for a slash-pass-through runtime, **Then** the authoritative snapshot stores the raw instructions unchanged and records a `slash_command` invocation with `command=review`, `detectionStatus=detected`, and `sourcePath` matching the task instruction field.
2. **Given** a step's instructions begin with `/simplify`, **When** the task is submitted, **Then** the authoritative snapshot records the command metadata on that step with the step `targetStepId` and a step source path such as `steps[0].instructions`.
3. **Given** instructions begin with an unknown valid command such as `/future-command`, **When** the selected runtime supports slash-command pass-through, **Then** the command is accepted as an opaque runtime invocation and is not rejected solely because no command hint exists.
4. **Given** instructions begin with escaped slash text such as `\/review`, **When** the task is submitted, **Then** the snapshot records escaped literal metadata and the command cannot later be rendered as an executable leading slash command by default.
5. **Given** instructions contain malformed ordinary path-like input or target a runtime without slash pass-through, **When** configured policy permits warning literal handling, **Then** authored instructions are preserved and the snapshot records non-executable metadata or warning state without rewriting user text.
6. **Given** frontend-supplied command metadata conflicts with backend parsing, **When** the task is submitted, **Then** backend normalization remains authoritative and rejects or replaces the inconsistent metadata according to validation policy.

### Edge Cases

- Leading whitespace before a slash command is treated as ordinary authored text and does not produce a detected command.
- Slash commands with arguments preserve both the raw first line and the parsed argument string.
- Unknown provider command forms that are slash-leading but outside the structured grammar remain opaque only when they are not clearly ordinary path text and the runtime supports pass-through.
- Empty instruction fields do not produce command metadata.
- Multiple instruction-bearing locations in one task can each carry independent source paths without conflating task-level and step-level commands.

## Assumptions

- The selected story covers backend normalization and authoritative snapshot data only; provider-neutral Create page previews are tracked separately by related issue MM-685.
- Runtime-specific rendering is not completed by this story, but the snapshot metadata must be sufficient for later adapter-owned rendering.
- The default validation posture follows the source design: pass through valid slash commands for slash-capable runtimes, allow escaped literals, and warn or reject malformed or unsupported-runtime input according to configured policy.

## Source Design Requirements

- **DESIGN-REQ-001** *(docs/Steps/SlashCommands.md Purpose, Goals)*: The system must detect leading slash commands from normal task or step instructions while preserving the authored instruction text. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-002** *(Design Principles: Preserve user intent exactly)*: Command detection must not rewrite, alias, or reinterpret the user's authored command token or instruction body. Scope: in scope. Maps to FR-001, FR-008.
- **DESIGN-REQ-003** *(Design Principles: Treat slash commands as structured task metadata)*: A leading slash command must be represented as structured runtime command metadata rather than only prompt text. Scope: in scope. Maps to FR-002, FR-003, FR-004.
- **DESIGN-REQ-004** *(Domain Model: RuntimeCommandInvocation)*: Runtime command metadata must include command identity, raw command, arguments, instruction body, source path, target runtime or step when relevant, detection status, hint status, recognition mode, runtime recognition requirement, capability or hint versions, and detection phase. Scope: in scope. Maps to FR-003, FR-004.
- **DESIGN-REQ-005** *(Task Input Shape)*: The authoritative task input snapshot must support runtime command metadata at task-level and step-level instruction locations. Scope: in scope. Maps to FR-002, FR-004.
- **DESIGN-REQ-006** *(Detection Rules)*: Detection must be conservative, first-character slash based, support the documented grammar, and handle opaque slash-leading command forms for pass-through runtimes when they are not ordinary path-like text. Scope: in scope. Maps to FR-005, FR-006.
- **DESIGN-REQ-007** *(Unknown Command Contract)*: Unknown valid commands must remain first-class runtime invocations for slash-pass-through runtimes and must not be rejected solely because MoonMind lacks a hint. Scope: in scope. Maps to FR-006.
- **DESIGN-REQ-008** *(Escape Behavior)*: Escaped leading slash text must be represented as escaped literal metadata and must not later render as an executable leading slash command by default. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-010** *(Backend Behavior, Validation policy)*: Backend submit-time normalization must be authoritative over frontend preview metadata and must validate runtime capability, command grammar, policy, and supplied metadata before building the snapshot. Scope: in scope. Maps to FR-008, FR-009.
- **DESIGN-REQ-019** *(Non-goals and command hints)*: Command hints may enrich metadata but must not act as a blocking allowlist; Create-page provider-specific previews and runtime-specific command rendering are out of this story unless needed to preserve snapshot contracts. Scope: in scope for non-blocking hints, out of scope for preview UI/rendering. Maps to FR-006, FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST preserve user-authored task-level and step-level instruction text in the authoritative task input snapshot when leading slash detection occurs.
- **FR-002**: The system MUST detect leading slash command candidates independently for task-level instructions and each step instruction field that participates in task submission.
- **FR-003**: The system MUST record a structured `RuntimeCommandInvocation` for detected task-level commands with deterministic command, raw command, arguments, instruction body, source path, target runtime, detection status, hint status, recognition mode, runtime recognition requirement, capability or hint version, and detection phase values.
- **FR-004**: The system MUST record step-level runtime command metadata with a step source path and target step identifier when a step instruction begins with a detected slash command.
- **FR-005**: The system MUST parse slash-leading command lines with the documented conservative grammar and preserve opaque first-line command forms for pass-through runtimes when they are not ordinary path-like text.
- **FR-006**: The system MUST accept unknown valid slash commands for slash-pass-through runtimes without requiring a known-command hint.
- **FR-007**: The system MUST represent escaped leading slash input as escaped literal command metadata that cannot be rendered later as an executable leading slash command by default.
- **FR-008**: The system MUST make backend normalization authoritative by rejecting or replacing malformed or inconsistent frontend-supplied runtime command metadata instead of trusting it blindly.
- **FR-009**: The system MUST apply configured validation policy for unsupported runtimes and malformed ordinary path-like input without rewriting authored instruction text.
- **FR-010**: The system MUST keep command hints as enrichment metadata only and must not treat absent hints as a rejection reason for valid pass-through commands.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-684` and this canonical Jira preset brief.

### Key Entities

- **Runtime Command Invocation**: Structured metadata derived from authored instructions, including command identity, parsed body, source location, runtime target, recognition and validation status, and versioned capability context.
- **Runtime Command Policy**: Configurable validation posture for slash-capable runtimes, runtimes without slash pass-through, escaped commands, and malformed command lines.
- **Authoritative Task Input Snapshot**: The preserved task submission payload that stores authored instruction text and derived runtime command metadata for workflow execution, audit, edit, and rerun.
- **Command Hint**: Optional enrichment metadata for known commands that can improve labels or descriptions without becoming an allowlist.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A submitted task with `/review` as the first task instruction characters produces preserved task instructions and a detected `slash_command` invocation with `command=review` and the task instruction source path.
- **SC-002**: A submitted task with `/simplify` as the first step instruction characters produces preserved step instructions and step command metadata containing the correct target step identifier and `steps[0].instructions`-style source path.
- **SC-003**: Unknown valid commands such as `/future-command` are accepted for slash-pass-through runtimes with opaque hint status and no allowlist rejection.
- **SC-004**: Escaped input such as `\/review` records escaped literal metadata and does not leave an executable leading slash command as the default runtime-visible representation.
- **SC-005**: Malformed ordinary path-like input and runtimes without slash pass-through follow configured warning or rejection policy while preserving authored instructions in any accepted snapshot.
- **SC-006**: Inconsistent frontend-supplied command metadata is rejected or normalized by backend authority, with test evidence covering missing metadata, conflicting metadata, and malformed metadata.
- **SC-007**: Traceability evidence preserves `MM-684`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-008, DESIGN-REQ-010, and DESIGN-REQ-019 in MoonSpec artifacts and verification evidence.
