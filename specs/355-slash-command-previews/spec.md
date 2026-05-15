# Feature Specification: Provider-Neutral Slash Command Previews

**Feature Branch**: `355-slash-command-previews`
**Created**: 2026-05-15
**Status**: Draft
**Input**:

```text
# MM-685 MoonSpec Orchestration Input

## Source

- Jira issue: MM-685
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Show provider-neutral slash command previews on the Create page
- Labels: `moonmind-workflow-mm-1c30567c-221e-4dc1-bc74-d1248e750656`
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:9f8378c1-5596-4d43-875b-8387e0bedb86/artifacts/moonspec-inputs/MM-685-trusted-jira-get-issue.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`; potentially related custom fields `Implementation plan`, `Backout plan`, and `Test plan` were present but empty.

## Canonical MoonSpec Feature Request

Jira issue: MM-685 from MM project
Summary: Show provider-neutral slash command previews on the Create page
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-685 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-685: Show provider-neutral slash command previews on the Create page

Source Reference
Source Document: docs/Steps/SlashCommands.md
Source Title: Runtime Slash Commands on Create Task
Source Sections:
- Design Principles
- Runtime Capabilities and Command Hints
- Create Page Behavior
- Edit mode
- Non-goals
- Testing Strategy
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-018
- DESIGN-REQ-019

As a user composing a task, I want the Create page to preview whether my leading slash text will be treated as a runtime command, literal text, or unsupported runtime case, so I can submit the task intentionally without MoonMind hard-coding provider command markup.

Acceptance Criteria
- Leading /review shows a runtime command status for a slash-capable runtime and may include hint text when available.
- Unknown valid /foo shows pass-through status rather than a warning or error when the selected runtime supports slash commands.
- Selecting a runtime without slash-command pass-through recomputes recognition mode and shows an actionable warning without mutating authored instructions.
- Escaped \/review does not show an executable command chip and is represented as literal text intent.
- Create page code consumes declarative capability and hint metadata and does not embed Codex-specific or Claude-specific command markup.

Requirements
- Surface runtimeCommand preview state for objective and step instructions when the first character is /.
- Use runtime capability catalog data and hint catalog data for preview labels and descriptions.
- Recompute preview when selected runtime changes without altering the underlying instruction value.
- Keep provider-specific rendering decisions out of the Create page.

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path: `docs/Steps/SlashCommands.md`.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

Preserved source Jira preset brief: `MM-685` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-685` and local handoff `/work/agent_jobs/mm:9f8378c1-5596-4d43-875b-8387e0bedb86/artifacts/moonspec-inputs/MM-685-canonical-moonspec-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-685` implementation brief was found under `specs/`; `Specify` is the first incomplete stage.

## User Story - Preview Slash Command Intent

**Summary**: As a user composing a task, I want the Create page to preview whether slash-leading instructions will run as a runtime command, literal text, or an unsupported runtime case so that I can submit task instructions intentionally.

**Goal**: Users can see a provider-neutral interpretation of leading slash text before submission, including known-command hints when available, opaque pass-through for unknown valid commands, unsupported-runtime warnings, and escaped-literal behavior.

**Independent Test**: Can be fully tested by composing task-level and step-level instructions that begin with known, unknown, unsupported, and escaped slash text, switching between slash-capable and non-slash-capable runtimes, and verifying that preview state and authored instructions remain correct without submitting a task.

**Acceptance Scenarios**:

1. **Given** a slash-capable runtime is selected, **When** the user enters `/review` as the first characters of task or step instructions, **Then** the Create page shows a runtime command preview and may show known-command hint text when hint data exists.
2. **Given** a slash-capable runtime is selected, **When** the user enters an unknown valid command such as `/foo`, **Then** the Create page shows pass-through runtime command status without treating the missing hint as a warning or error.
3. **Given** a runtime without slash-command pass-through is selected, **When** the user enters slash-leading instructions, **Then** the Create page recomputes recognition mode and shows an actionable warning without changing the authored text.
4. **Given** the user enters escaped slash text such as `\/review`, **When** the preview is evaluated, **Then** the Create page does not show an executable command chip and presents the input as literal text intent.
5. **Given** a task is opened for edit, **When** authored instructions and any stored runtime command metadata are available, **Then** the Create page restores the preview from authoritative task input data or re-detects for preview only without altering historical authored instructions.

### Edge Cases

- Slash text preceded by whitespace is treated as normal text rather than a detected leading command.
- Path-like text such as `/src/app.ts is broken` is treated as malformed or literal text rather than a pass-through command preview.
- A command token with no local hint remains previewable as opaque pass-through when the runtime supports slash commands.
- Runtime changes recompute only preview state and must not mutate the instruction field.
- Missing or stale stored command metadata during edit mode may be used for preview re-detection only and must not rewrite saved instructions.

## Assumptions

- The runtime capability catalog and optional command hint catalog are the authoritative sources for preview labels, descriptions, and slash-command support state.
- The preview behavior applies to both task-level objective instructions and individual step instructions because the Jira brief names both objective and step instructions.
- This story covers Create page preview and edit-mode presentation only; backend normalization, final runtime rendering, workflow submission behavior, and runtime adapter execution are separate source-design slices.

## Source Design Requirements

| Requirement ID | Source Citation | Requirement Summary | Scope | Mapped Requirement |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Steps/SlashCommands.md` lines 50-70, Design Principles | The Create page must preserve authored slash-leading text exactly, use declarative runtime capability and hint data, and avoid provider-specific command markup or per-provider conditionals. | In scope | FR-001, FR-002, FR-008 |
| DESIGN-REQ-002 | `docs/Steps/SlashCommands.md` lines 78-87, Goals | Users must be able to compose slash-leading task instructions, see detection based on the first character, and use an escape hatch for literal slash text. | In scope | FR-001, FR-005, FR-006 |
| DESIGN-REQ-003 | `docs/Steps/SlashCommands.md` lines 91-99, Non-goals | The preview must not require MoonMind to know every runtime command, must not replace runtime-native command systems, and must not block unknown commands only because no local hint exists. | In scope | FR-003, FR-008 |
| DESIGN-REQ-004 | `docs/Steps/SlashCommands.md` lines 287-316, Detection Rules | Preview detection must distinguish detected commands, whitespace-prefixed non-commands, escaped literals, path-like malformed text, unknown valid commands, and inline slash text. | In scope | FR-001, FR-003, FR-005, FR-006, FR-007 |
| DESIGN-REQ-005 | `docs/Steps/SlashCommands.md` lines 318-329, Unknown Command Contract | Unknown valid slash commands must be first-class pass-through invocations for runtimes with slash-command pass-through and must not produce warnings or errors because of missing hints. | In scope | FR-003 |
| DESIGN-REQ-006 | `docs/Steps/SlashCommands.md` lines 372-391, Runtime Capabilities and Command Hints | Slash-command support is runtime-level capability data, and known-command hints may enrich labels, descriptions, autocomplete, and examples without becoming validation gates. | In scope | FR-002, FR-003, FR-004 |
| DESIGN-REQ-007 | `docs/Steps/SlashCommands.md` lines 499-532, Create Page Behavior | The Create page must show runtime command status, optional known-command hints, pass-through status for unknown commands, and actionable warnings for runtimes without slash-command pass-through. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-008 | `docs/Steps/SlashCommands.md` lines 534-538, Runtime changes | Changing the selected runtime must recompute recognition mode without changing authored instructions. | In scope | FR-004, FR-008 |
| DESIGN-REQ-009 | `docs/Steps/SlashCommands.md` lines 540-547, Edit mode | Edit mode must restore authored instructions and command metadata from the task input snapshot when present, and re-detection must not silently alter the historical raw instruction value. | In scope | FR-009 |
| DESIGN-REQ-010 | `docs/Steps/SlashCommands.md` lines 861-895, Testing Strategy | Parser and Create page validation must cover known commands, unknown commands, whitespace-prefixed input, escaped literals, malformed path-like text, runtime changes, and preservation of authored instructions. | In scope | FR-010 |
| DESIGN-REQ-011 | `docs/Steps/SlashCommands.md` lines 555-638, Backend behavior and runtime preparation pipeline | Backend authoritative normalization and final runtime rendering after context preparation are required by the broader design. | Out of scope for this Create page preview story; handled by separate runtime/backend slices. | None |
| DESIGN-REQ-012 | `docs/Steps/SlashCommands.md` lines 672-717, Runtime strategy integration | Runtime-specific Codex and Claude renderers decide how to make slash commands recognized during execution. | Out of scope for this Create page preview story; handled by separate runtime adapter slices while this story keeps the Create page provider-neutral. | None |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST surface runtime command preview state for task-level objective instructions and step instructions when the first character is `/`.
- **FR-002**: The preview MUST use runtime capability data and optional known-command hint data to determine preview labels, descriptions, and slash-command support state.
- **FR-003**: For slash-capable runtimes, an unknown valid command such as `/foo` MUST show pass-through status and MUST NOT be presented as a warning, error, or blocked submission solely because no hint exists.
- **FR-004**: When the selected runtime changes, the preview MUST recompute recognition mode and any unsupported-runtime warning without changing the authored instruction value.
- **FR-005**: Escaped slash-leading text such as `\/review` MUST be represented as literal text intent and MUST NOT show an executable command chip.
- **FR-006**: The preview MUST distinguish whitespace-prefixed slash text and inline slash text from leading slash commands.
- **FR-007**: The preview MUST distinguish path-like or malformed slash-leading text from valid pass-through runtime commands and present the resulting literal or warning state without rewriting the user's text.
- **FR-008**: The Create page MUST NOT embed Codex-specific, Claude-specific, or provider-specific command markup or rendering decisions in preview behavior.
- **FR-009**: In edit mode, the Create page MUST restore authored instructions and stored runtime command metadata from the authoritative task input snapshot when present, and preview-only re-detection MUST NOT alter the historical instruction value.
- **FR-010**: Verification coverage MUST include known commands, unknown valid commands, non-slash-capable runtimes, escaped literals, runtime changes, whitespace-prefixed input, path-like malformed input, task-level instructions, step-level instructions, and edit-mode restoration.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-685` and the original Jira preset brief.

### Key Entities

- **Runtime Command Preview**: The user-visible interpretation of slash-leading authored instructions, including detection status, hint status, recognition mode, literal status, warning status, and source location.
- **Runtime Capability**: Declarative runtime metadata indicating whether slash-command pass-through is supported and which hint catalog may enrich previews.
- **Command Hint**: Optional declarative metadata for a known command, including user-facing labels and descriptions that enrich previews without acting as an allowlist.
- **Authored Instructions**: The task-level or step-level instruction text exactly as entered or restored for editing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested known slash-command examples at task and step level show a runtime command preview for slash-capable runtimes.
- **SC-002**: 100% of tested unknown valid slash-command examples for slash-capable runtimes show pass-through status without warning or error language caused by missing hints.
- **SC-003**: 100% of tested runtime changes recompute preview state while preserving the exact authored instruction text.
- **SC-004**: 100% of tested escaped slash-leading examples are represented as literal text intent and do not show an executable command chip.
- **SC-005**: 100% of tested non-slash-capable runtime cases show an actionable unsupported-runtime warning without changing authored instructions.
- **SC-006**: Traceability review confirms `MM-685`, the original Jira preset brief, and all in-scope source design requirements remain present in MoonSpec artifacts and final verification evidence.
