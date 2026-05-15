# Feature Specification: Runtime Command Rendering After Context Preparation

**Feature Branch**: `356-runtime-command-rendering`
**Created**: 2026-05-15
**Status**: Draft
**Input**:

```text
# MM-686 MoonSpec Orchestration Input

Use the Jira preset brief for MM-686 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): docs/Steps/SlashCommands.md.

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-686 MoonSpec Orchestration Input

## Source

- Jira issue: MM-686
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Render runtime commands after context preparation in managed runtime adapters
- Priority: Medium
- Labels: `moonmind-workflow-mm-1c30567c-221e-4dc1-bc74-d1248e750656`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-686 from MM project
Summary: Render runtime commands after context preparation in managed runtime adapters
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-686 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-686: Render runtime commands after context preparation in managed runtime adapters

Source Reference
Source Document: docs/Steps/SlashCommands.md
Source Title: Runtime Slash Commands on Create Task
Source Sections:
- Runtime Render Modes
- Runtime Preparation Pipeline
- Runtime Strategy Integration
- Codex CLI rendering
- Claude Code rendering
- Failure Modes
- Security Requirements
- Testing Strategy

Coverage IDs:
- DESIGN-REQ-006
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-016
- DESIGN-REQ-017
- DESIGN-REQ-018
- DESIGN-REQ-019

As a managed runtime operator, I want Codex CLI, Claude Code, and future adapters to render slash commands only after MoonMind has prepared context, so runtime command recognition is preserved even when retrieval, skills, and runtime notes are added.

Acceptance Criteria
- Rendering occurs after retrieval context, skill activation summaries, and managed runtime notes are prepared.
- Codex CLI prompt-prefix output starts with /review followed by the instruction body and prepared context.
- Claude Code prompt-prefix output starts with /review and does not require the Create page to know its render mode.
- Unknown valid commands remain slash-leading in prompt-prefix or generic native-command transports and are never materialized.
- Materialized command mode writes only allowlisted files for commands with explicit known-command metadata.
- Escaped literal commands render with a non-command prefix, quote block, or runtime-specific literal wrapper so the runtime does not execute the slash command.
- Renderer failures produce typed user_error runtime_command_render_failed results or a policy-approved fallback event before launch.

Requirements
- Introduce an adapter-owned renderer contract that can produce RuntimeCommandRenderResult values.
- Support plain_prompt, prompt_prefix, native_command, materialized_command, and unsupported outcomes as documented.
- Ensure MoonMind-added context never precedes a command that requires first-character recognition.
- Treat command names, args, and bodies as untrusted text and avoid direct shell command construction.
```

Preserved source Jira preset brief: `MM-686` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-686` and local handoff `/work/agent_jobs/mm:03ecc585-89e8-4589-8bdf-05bc41f57e4a/artifacts/moonspec-orchestration-input-MM-686.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserving the `MM-686` runtime rendering brief was found under `specs/`; `Specify` is the first incomplete stage.

## User Story - Preserve Slash Command Recognition At Runtime Launch

**Summary**: As a managed runtime operator, I want slash commands to be rendered only after MoonMind has prepared retrieval context, skill summaries, and runtime notes so that supported runtimes still recognize the command as a command while preserving prepared context.

**Goal**: Managed runtime launches keep the slash command in the runtime-required recognition position after all MoonMind context preparation is complete, while preserving literal-command escapes, safe fallback behavior, and runtime-specific rendering boundaries.

**Independent Test**: Can be fully tested by submitting command-leading tasks for slash-capable runtimes, with retrieval context, skill activation summaries, and managed runtime notes present, then verifying that the final runtime-visible input starts with the command when required and that unsupported, escaped, unknown, materialized, and failure cases produce the documented outcomes before launch.

**Acceptance Scenarios**:

1. **Given** a slash-capable runtime using prompt-prefix command recognition and a task whose authored instructions start with `/review`, **When** MoonMind prepares retrieval context, skill activation summaries, and runtime notes, **Then** the final runtime-visible input starts with `/review`, followed by the instruction body and then prepared context.
2. **Given** the same prompt-prefix command is launched through Codex CLI or Claude Code, **When** runtime-specific rendering occurs, **Then** the Create page does not need to know that runtime's command markup and the command remains the first runtime-visible text.
3. **Given** an unknown valid slash command is submitted to a slash-capable runtime, **When** rendering occurs, **Then** the command remains slash-leading through prompt-prefix or native command transport and is not materialized into files.
4. **Given** a known command supports materialized command mode, **When** rendering requires file materialization, **Then** only allowlisted materialization targets are written and unknown commands are excluded from materialized mode.
5. **Given** the user escaped a slash-leading command to make it literal, **When** final rendering occurs, **Then** the runtime receives non-command literal instructions through a safe prefix, quote block, or equivalent runtime-specific literal wrapper.
6. **Given** runtime-specific command rendering fails before launch, **When** policy does not allow silent literal submission, **Then** the launch stops with a typed user-facing runtime command rendering failure or records a policy-approved fallback event before the agent starts.

### Edge Cases

- Retrieval context, skill activation summaries, or managed runtime notes are empty; the renderer still preserves the correct command recognition position.
- Prepared context is large or multi-part; it is appended after the command and instruction body for prompt-prefix recognition instead of being prepended.
- The selected runtime does not support slash-command pass-through; rendering follows unsupported-runtime policy without pretending the command executed.
- An unknown command looks valid but has no local hint; it remains an opaque pass-through command for slash-capable runtimes.
- A command name, argument, or body contains text that resembles shell syntax; it is treated only as untrusted authored text unless an allowlisted renderer explicitly handles it.
- A materialized command target is outside the allowlist; rendering fails before launch rather than writing the file.

## Assumptions

- Backend command normalization and Create page preview behavior from the preceding slash-command stories are available as inputs to this runtime rendering story.
- The story covers managed runtime launch behavior for Codex CLI, Claude Code, and future runtimes with equivalent capability metadata; provider-specific internals remain owned by the runtime adapter boundary.
- Literal fallback is allowed only when runtime policy explicitly permits it and the event remains auditable.

## Source Design Requirements

| Requirement ID | Source Citation | Requirement Summary | Scope | Mapped Requirement |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-006 | `docs/Steps/SlashCommands.md` lines 437-455, Runtime Render Modes | Runtime rendering must support literal prompt behavior, prompt-prefix command recognition, and structured native command transport as distinct outcomes. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-011 | `docs/Steps/SlashCommands.md` lines 616-638, Runtime Preparation Pipeline | Runtime command rendering must happen after context preparation and before the command is passed to the runtime so prepared context cannot move the slash command out of recognition position. | In scope | FR-001, FR-005, FR-006 |
| DESIGN-REQ-012 | `docs/Steps/SlashCommands.md` lines 639-671, Runtime Strategy Integration | Runtime strategies must own command rendering through a boundary that can report render mode, rendered content, unsupported outcomes, and failure state. | In scope | FR-002, FR-004, FR-011 |
| DESIGN-REQ-013 | `docs/Steps/SlashCommands.md` lines 672-717, Codex CLI and Claude Code rendering | Codex CLI and Claude Code prompt-prefix rendering must put the slash command before instruction body and prepared context without requiring Create page provider markup. | In scope | FR-003, FR-007 |
| DESIGN-REQ-016 | `docs/Steps/SlashCommands.md` lines 318-329 and 435-455, Unknown commands and render modes | Unknown valid commands must remain slash-leading for slash-capable runtimes and must not be materialized into command files. | In scope | FR-008, FR-009 |
| DESIGN-REQ-017 | `docs/Steps/SlashCommands.md` lines 336-370 and 437-448, Escaped and literal slash text | Escaped slash-leading text must render as literal instructions and must not leave an executable command at the first runtime-visible position. | In scope | FR-010 |
| DESIGN-REQ-018 | `docs/Steps/SlashCommands.md` lines 779-790, Security Requirements | Rendering must not expose secrets, must treat user command text as untrusted, must keep backend validation authoritative, and must not use hints as a blocking allowlist. | In scope | FR-012, FR-013, FR-014 |
| DESIGN-REQ-019 | `docs/Steps/SlashCommands.md` lines 792-859 and 861-946, Failure Modes and Testing Strategy | Renderer failures, unsupported runtimes, context-order regressions, and runtime-specific prompt-prefix cases must be observable and covered by validation. | In scope | FR-006, FR-011, FR-015 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST render runtime commands only after retrieval context, skill activation summaries, managed runtime notes, and other MoonMind-prepared context are available.
- **FR-002**: System MUST route runtime command rendering through a runtime-owned boundary that can produce plain prompt, prompt-prefix, native command, materialized command, unsupported, and failed outcomes.
- **FR-003**: For prompt-prefix command recognition, System MUST place the slash command before the instruction body and any MoonMind-prepared context in the final runtime-visible input.
- **FR-004**: For plain prompt outcomes, System MUST send literal instructions as normal text without presenting the text as an executable runtime command.
- **FR-005**: System MUST ensure MoonMind-added context never precedes a command that requires first-character or first-token recognition by the selected runtime.
- **FR-006**: System MUST fail before launching the agent or record a policy-approved fallback event when rendering cannot safely produce the requested runtime command outcome.
- **FR-007**: Codex CLI and Claude Code runtime paths MUST support prompt-prefix slash-command rendering without requiring Create page logic to know provider-specific command markup.
- **FR-008**: Unknown valid slash commands MUST remain slash-leading through prompt-prefix or native command transport for slash-capable runtimes.
- **FR-009**: Unknown valid slash commands MUST NOT be rendered through materialized command mode or used to create runtime command files.
- **FR-010**: Escaped literal slash commands MUST render with a non-command prefix, quote block, or runtime-specific literal wrapper so the runtime does not execute the slash command.
- **FR-011**: Runtime command rendering failures MUST surface a typed user-facing failure reason or an auditable fallback event before process launch or turn submission.
- **FR-012**: Runtime renderers MUST treat command names, command arguments, and instruction bodies as untrusted authored text.
- **FR-013**: Runtime command rendering MUST NOT expose secrets in final rendered input, diagnostics, or audit output.
- **FR-014**: Known-command hints MUST NOT become a blocking allowlist for pass-through runtimes.
- **FR-015**: Validation coverage MUST prove that retrieval context, skill activation summaries, and managed runtime notes do not appear before slash commands that require first-position recognition.
- **FR-016**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-686` and the original Jira preset brief.

### Key Entities

- **Runtime Command Invocation**: The structured interpretation of authored slash-leading instructions, including command token, arguments, instruction body, source location, and whether the command is known or opaque.
- **Runtime Render Outcome**: The rendering result selected by a runtime boundary, including final user-visible input shape, render mode, unsupported-runtime state, failure reason, or fallback event.
- **Prepared Runtime Context**: MoonMind-added retrieval context, skill activation summaries, managed runtime notes, and other launch-time context that must be positioned after slash commands requiring first-position recognition.
- **Runtime Capability**: Declarative runtime metadata indicating whether slash-command pass-through, prompt-prefix rendering, native command transport, or materialized command rendering is supported.
- **Materialized Command Target**: A runtime-owned file or artifact location where a known command may be written only when explicitly allowed by policy and renderer metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of tested prompt-prefix launches with `/review` for slash-capable Codex CLI and Claude Code paths begin with `/review` before instruction body and prepared context.
- **SC-002**: 100% of tested launches with retrieval context, skill activation summaries, and managed runtime notes preserve command recognition order when the selected runtime requires first-position slash recognition.
- **SC-003**: 100% of tested unknown valid slash commands for slash-capable runtimes remain slash-leading through prompt-prefix or native command transport and are not materialized.
- **SC-004**: 100% of tested escaped literal slash commands render as non-executable literal instructions.
- **SC-005**: 100% of tested rendering failure cases stop before launch with a typed failure or record an auditable policy-approved fallback event.
- **SC-006**: 100% of tested materialized command cases write only allowlisted targets, and 0 unknown commands are materialized.
- **SC-007**: Traceability review confirms `MM-686`, the original Jira preset brief, and all in-scope source design requirements remain present in MoonSpec artifacts and final verification evidence.
