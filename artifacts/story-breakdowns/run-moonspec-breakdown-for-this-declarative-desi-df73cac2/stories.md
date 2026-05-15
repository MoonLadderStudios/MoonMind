# Story Breakdown: Runtime Slash Commands on Create Task

- Source design: `docs/Steps/SlashCommands.md`
- Original source reference path: `docs/Steps/SlashCommands.md`
- Story extraction date: `2026-05-15T03:33:23Z`
- Requested output mode: `jira`

## Design Summary

The design defines desired-state support for passing leading slash commands from Create Task instructions through MoonMind into managed runtimes. MoonMind preserves raw authored instructions, derives structured RuntimeCommandInvocation metadata at submission, lets runtimes declare slash-command capabilities and hints, and renders commands at the final adapter boundary after context preparation. It explicitly keeps provider command semantics outside Create page logic, supports unknown opaque commands for capable runtimes, preserves edit/rerun/audit fidelity, and requires security and regression coverage across parser, UI, backend, and runtime rendering paths.

## Coverage Points

- `DESIGN-REQ-001` (requirement) **Pass runtime slash commands from Create Task instructions** — Users can start task or step instructions with runtime-native slash commands such as /review or /simplify and have MoonMind preserve their runtime-command behavior. Source: Purpose; Goals; Acceptance Criteria.
- `DESIGN-REQ-002` (state-model) **Preserve authored instructions exactly through snapshots** — Slash command parsing derives metadata without rewriting the raw authored instruction text stored in the authoritative task input snapshot. Source: Design Principles; Task Input Shape.
- `DESIGN-REQ-003` (state-model) **Represent leading slash commands as structured metadata** — MoonMind stores RuntimeCommandInvocation metadata including command, args, body, source path, target runtime or step, detection status, hint status, recognition mode, version metadata, and detection phase. Source: Terminology; Domain Model; Task Input Shape.
- `DESIGN-REQ-004` (requirement) **Detect task-level and step-level leading slash commands conservatively** — A command is detected only when the instruction is non-empty and begins with /, with source paths for objective or step instruction fields and parser behavior for known, opaque, escaped, malformed, and non-leading examples. Source: Task Input Shape; Detection Rules.
- `DESIGN-REQ-005` (integration) **Unknown valid commands pass through on capable runtimes** — Unknown slash commands are first-class opaque runtime invocations, are not rejected for missing hints, remain slash-leading, and are not materialized into command files. Source: Unknown Command Contract; Failure Modes.
- `DESIGN-REQ-006` (requirement) **Escaped slash-leading text remains literal** — Users can write escaped commands such as \/review to force literal text, and rendering must avoid leaving the unescaped slash command first in runtime-visible input. Source: Escape Behavior; Runtime Render Modes.
- `DESIGN-REQ-007` (constraint) **Runtime capability and policy determine recognition mode** — Slash-command support is declared at runtime level through capability and policy catalogs rather than per-command frontend logic or allowlists. Source: Runtime Capabilities and Command Hints; Validation policy.
- `DESIGN-REQ-008` (constraint) **Known-command hints enrich UX without becoming validation gates** — Hint catalog entries can provide labels, descriptions, examples, aliases, and future materialization metadata, but absence of a hint must not block pass-through runtimes. Source: Design Principles; Runtime Capabilities and Command Hints; Create Page Behavior.
- `DESIGN-REQ-009` (ui) **Create Page previews command interpretation without provider-specific rendering** — The Create page shows pass-through, hint, unsupported-runtime, runtime-change, and escape statuses while keeping runtime-specific command markup out of React conditionals. Source: Create Page Behavior; Non-goals.
- `DESIGN-REQ-010` (integration) **Backend normalization is authoritative** — Submit-time backend logic detects, validates, canonicalizes, and stores runtime command metadata even when frontend metadata is absent or malformed. Source: Backend Behavior.
- `DESIGN-REQ-011` (integration) **Render commands at the last responsible runtime boundary** — Runtime command rendering occurs after RAG injection, skill activation, runtime notes, and other instruction preparation so MoonMind-added context cannot move the command away from the recognition position. Source: Runtime Preparation Pipeline.
- `DESIGN-REQ-012` (integration) **Runtime adapters own render modes and materialization** — Adapters expose renderer behavior for plain prompt, prompt prefix, native command, materialized command, and unsupported modes; opaque commands can use only pass-through transports. Source: Runtime Render Modes; Runtime Strategy Integration.
- `DESIGN-REQ-013` (integration) **Codex CLI and Claude Code preserve prompt-prefix recognition** — Codex CLI and Claude Code prompt-prefix rendering must place slash commands as the first runtime-visible characters, with Claude materialized mode reserved for explicit known-command metadata. Source: Runtime Strategy Integration.
- `DESIGN-REQ-014` (state-model) **Edit and rerun preserve historical command fidelity** — Edit mode restores authored instructions and metadata from snapshots, rerun preserves original command metadata and versions, and edit-for-rerun may recompute warnings without altering the source run. Source: Create Page Behavior; Edit mode; Rerun mode.
- `DESIGN-REQ-015` (observability) **Audit, observability, and task details expose both authored text and interpretation** — Runtime command detection, rendering, pass-through, versions, and task details should be visible without obscuring the original authored instructions. Source: Audit and Observability.
- `DESIGN-REQ-016` (security) **Security rules prevent command injection and unsafe materialization** — User command names and args are untrusted text, must not directly construct shell commands, must not expose secrets, and materialized files must be allowlisted by renderers. Source: Security Requirements.
- `DESIGN-REQ-017` (resiliency) **Failure modes are typed and policy controlled** — Unknown commands, unsupported runtimes, renderer failures, and context-preparation ordering issues have explicit default behaviors, typed failures, or policy-approved fallback semantics. Source: Failure Modes.
- `DESIGN-REQ-018` (verification) **Testing spans parser, Create page, backend, rendering, edit, and rerun paths** — The design requires parser, Create page, backend, runtime rendering, edit/rerun, security, and acceptance coverage aligned with the final behavior. Source: Testing Strategy; Acceptance Criteria.
- `DESIGN-REQ-019` (non-goal) **Non-goals keep MoonMind out of provider command semantics** — The system does not require MoonMind to know every command, replace runtime-native command systems, make commands workflow actions by default, infer command semantics after context injection, or block unknown commands due to missing hints. Source: Non-goals.

## Ordered Story Candidates

### STORY-001 — Normalize slash-leading instructions into authoritative runtime command snapshots

- Short name: `runtime-command-snapshot`
- Source reference: `docs/Steps/SlashCommands.md`
- Source sections: Purpose, Problem, Design Principles, Goals, Terminology, Domain Model, Task Input Shape, Detection Rules, Unknown Command Contract, Escape Behavior, Backend Behavior, Validation policy, Non-goals
- Why: This establishes the durable contract that every later UI, rerun, and runtime-rendering surface depends on.
- Description: As a MoonMind operator submitting a task, I want slash-leading task and step instructions to be preserved as authored text while the backend records structured runtime command metadata, so managed runtimes can later recognize the command without losing audit fidelity.
- Independent test: Submit task drafts with objective-level and step-level examples from the design, then assert the authoritative task input snapshot preserves instructions and stores the expected RuntimeCommandInvocation fields without relying on frontend metadata.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Server-side parser for objective and step instruction fields
  - RuntimeCommandInvocation snapshot metadata
  - Runtime capability and policy lookup during submit normalization
  - Unknown, escaped, malformed, unsupported-runtime, hinted, and opaque command classifications
- Out of scope:
  - Create page visual design beyond consuming returned metadata
  - Runtime-specific final prompt rendering
  - Creating provider command files
- Acceptance criteria:
  - Submitting instructions beginning with /review stores the raw instructions unchanged and a slash_command RuntimeCommandInvocation with command=review, detectionStatus=detected, and a sourcePath matching the instruction field.
  - Submitting step instructions beginning with /simplify stores targetStepId and a step sourcePath such as steps[0].instructions.
  - Unknown valid commands such as /future-command are accepted as opaque runtime invocations for slash-pass-through runtimes and are never rejected solely because no hint exists.
  - Escaped input such as \/review is stored as escaped_literal metadata and cannot be rendered later as an executable leading slash command by default.
  - Malformed ordinary path-like inputs and runtimes without slash pass-through follow configured warning/reject policy without rewriting authored instructions.
  - Backend normalization rejects inconsistent or malformed frontend-supplied command metadata and remains authoritative when frontend metadata is missing.
- Requirements:
  - Implement a conservative parser matching the documented grammar and example table while preserving opaque first-line command forms for pass-through runtimes.
  - Add RuntimeCommandInvocation and related capability/hint/policy fields to the authoritative task input snapshot shape.
  - Compute detectionStatus, hintStatus, recognitionMode, requiresRuntimeRecognition, runtimeCapabilityVersion, hintCatalogVersion, sourcePath, command, args, and instructionBody deterministically.
  - Preserve exact user-authored instruction text according to existing task input snapshot rules.
  - Treat command hints as enrichment metadata and not as a blocking command allowlist.
- Source design coverage:
  - `DESIGN-REQ-001` — Defines the user-facing submission contract for slash-leading task and step instructions.
  - `DESIGN-REQ-002` — Owns backend snapshot preservation of raw authored instructions during submit normalization.
  - `DESIGN-REQ-003` — Owns the RuntimeCommandInvocation fields and authoritative snapshot shape.
  - `DESIGN-REQ-004` — Owns parser grammar, source paths, task and step detection, and escaped/malformed classification at submit time.
  - `DESIGN-REQ-005` — Owns pass-through treatment for unknown valid commands during detection and snapshot normalization.
  - `DESIGN-REQ-006` — Owns literal escaped command metadata at submission and renderer-safe literal output.
  - `DESIGN-REQ-007` — Owns runtime capability and policy inputs used to compute recognition modes.
  - `DESIGN-REQ-008` — Owns optional hint catalog behavior and prevents hints from becoming validation allowlists.
  - `DESIGN-REQ-010` — Owns authoritative server-side parser and validation behavior.
  - `DESIGN-REQ-019` — Owns non-goal enforcement through acceptance criteria and explicit out-of-scope boundaries.
- Risks or open questions:
  - The existing task input snapshot may have multiple creation paths; all submit paths need to use the same backend normalization boundary.
- Assumptions:
  - Runtime capability and hint catalog data can be introduced without new persistent storage.

### STORY-002 — Show provider-neutral slash command previews on the Create page

- Short name: `create-command-preview`
- Source reference: `docs/Steps/SlashCommands.md`
- Source sections: Design Principles, Runtime Capabilities and Command Hints, Create Page Behavior, Edit mode, Non-goals, Testing Strategy
- Why: This gives immediate user feedback while keeping Create as a schema and capability-driven composition surface.
- Description: As a user composing a task, I want the Create page to preview whether my leading slash text will be treated as a runtime command, literal text, or unsupported runtime case, so I can submit the task intentionally without MoonMind hard-coding provider command markup.
- Independent test: Render the Create page with slash-capable, non-capable, hinted, unknown, escaped, and runtime-change cases and assert the displayed status changes while the instruction textarea value remains unchanged.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Create page command chip/status preview
  - Known-command hint display
  - Unknown valid command pass-through display
  - Unsupported-runtime warning display
  - Runtime-change recomputation
  - Escaped command preview behavior
- Out of scope:
  - Authoritative backend validation
  - Runtime adapter prompt rendering
  - Provider-specific command materialization
- Acceptance criteria:
  - Leading /review shows a runtime command status for a slash-capable runtime and may include hint text when available.
  - Unknown valid /foo shows pass-through status rather than a warning or error when the selected runtime supports slash commands.
  - Selecting a runtime without slash-command pass-through recomputes recognition mode and shows an actionable warning without mutating authored instructions.
  - Escaped \/review does not show an executable command chip and is represented as literal text intent.
  - Create page code consumes declarative capability and hint metadata and does not embed Codex-specific or Claude-specific command markup.
- Requirements:
  - Surface runtimeCommand preview state for objective and step instructions when the first character is /.
  - Use runtime capability catalog data and hint catalog data for preview labels and descriptions.
  - Recompute preview when selected runtime changes without altering the underlying instruction value.
  - Keep provider-specific rendering decisions out of the Create page.
- Source design coverage:
  - `DESIGN-REQ-001` — Defines the user-facing submission contract for slash-leading task and step instructions.
  - `DESIGN-REQ-007` — Owns runtime capability and policy inputs used to compute recognition modes.
  - `DESIGN-REQ-008` — Owns optional hint catalog behavior and prevents hints from becoming validation allowlists.
  - `DESIGN-REQ-009` — Owns Create page preview, runtime change recomputation, unsupported warnings, and provider-neutral UI boundaries.
  - `DESIGN-REQ-018` — Owns the test suite coverage matrix for every parser, UI, backend, renderer, edit, and rerun scenario.
  - `DESIGN-REQ-019` — Owns non-goal enforcement through acceptance criteria and explicit out-of-scope boundaries.
- Risks or open questions:
  - Frontend preview may drift from backend normalization unless tests cover both sides against the same examples.
- Assumptions:
  - The Create page can obtain runtime capability and hint metadata through existing boot payload or settings data paths.

### STORY-003 — Render runtime commands after context preparation in managed runtime adapters

- Short name: `runtime-command-rendering`
- Source reference: `docs/Steps/SlashCommands.md`
- Source sections: Runtime Render Modes, Runtime Preparation Pipeline, Runtime Strategy Integration, Codex CLI rendering, Claude Code rendering, Failure Modes, Security Requirements, Testing Strategy
- Why: Parser metadata alone is insufficient; the command must remain first in the final runtime-visible input for prompt-prefix runtimes.
- Description: As a managed runtime operator, I want Codex CLI, Claude Code, and future adapters to render slash commands only after MoonMind has prepared context, so runtime command recognition is preserved even when retrieval, skills, and runtime notes are added.
- Independent test: Build AgentExecutionRequest inputs with command metadata plus prepared context, skill summaries, and runtime notes, then assert Codex and Claude rendered instruction refs start with the command and that unknown commands are passed through without materialized files.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - RuntimeCommandRenderer interface or equivalent adapter boundary
  - Final render point after RAG, skill activation summaries, and managed runtime notes
  - Codex CLI prompt_prefix rendering
  - Claude Code prompt_prefix and allowlisted materialized rendering
  - Renderer failure behavior
  - Security constraints for user command text
- Out of scope:
  - Create page previews
  - Backend parser implementation except consuming stored metadata
  - Adding MoonMind-owned slash command semantics beyond explicit known-command materialization metadata
- Acceptance criteria:
  - Rendering occurs after retrieval context, skill activation summaries, and managed runtime notes are prepared.
  - Codex CLI prompt-prefix output starts with /review followed by the instruction body and prepared context.
  - Claude Code prompt-prefix output starts with /review and does not require the Create page to know its render mode.
  - Unknown valid commands remain slash-leading in prompt-prefix or generic native-command transports and are never materialized.
  - Materialized command mode writes only allowlisted files for commands with explicit known-command metadata.
  - Escaped literal commands render with a non-command prefix, quote block, or runtime-specific literal wrapper so the runtime does not execute the slash command.
  - Renderer failures produce typed user_error runtime_command_render_failed results or a policy-approved fallback event before launch.
- Requirements:
  - Introduce an adapter-owned renderer contract that can produce RuntimeCommandRenderResult values.
  - Support plain_prompt, prompt_prefix, native_command, materialized_command, and unsupported outcomes as documented.
  - Ensure MoonMind-added context never precedes a command that requires first-character recognition.
  - Treat command names, args, and bodies as untrusted text and avoid direct shell command construction.
- Source design coverage:
  - `DESIGN-REQ-006` — Owns literal escaped command metadata at submission and renderer-safe literal output.
  - `DESIGN-REQ-011` — Owns the final render boundary after context, skills, and runtime notes are prepared.
  - `DESIGN-REQ-012` — Owns adapter renderer modes, generic native command constraints, materialization rules, and unsupported mode outputs.
  - `DESIGN-REQ-013` — Owns concrete Codex CLI and Claude Code rendering behavior and runtime-specific tests.
  - `DESIGN-REQ-016` — Owns security assertions across backend normalization and renderer behavior.
  - `DESIGN-REQ-017` — Owns typed or policy-controlled failure outcomes for unsupported, malformed, unknown, renderer, and context-order cases.
  - `DESIGN-REQ-018` — Owns the test suite coverage matrix for every parser, UI, backend, renderer, edit, and rerun scenario.
  - `DESIGN-REQ-019` — Owns non-goal enforcement through acceptance criteria and explicit out-of-scope boundaries.
- Risks or open questions:
  - Managed runtime launch paths may mutate instruction_ref in several places; the final render boundary must be placed after all existing mutations.
- Assumptions:
  - Codex CLI and Claude Code can both accept prompt text arranged by MoonMind without a new provider protocol.

### STORY-004 — Preserve slash command fidelity across edit, rerun, task details, and audit surfaces

- Short name: `command-fidelity-audit`
- Source reference: `docs/Steps/SlashCommands.md`
- Source sections: Create Page Behavior, Edit mode, Rerun mode, Audit and Observability, Testing Strategy, Acceptance Criteria
- Why: Slash commands affect runtime behavior, so edit/rerun and audit surfaces need exact historical reconstruction rather than best-effort re-detection.
- Description: As an operator reviewing or rerunning previous work, I want MoonMind to show the original authored instructions and the runtime command interpretation used at submission, so historical tasks remain auditable even as runtime capabilities or hints evolve.
- Independent test: Create a stored task input snapshot with runtimeCommand metadata and changed current hint/capability versions, then verify edit, rerun, task details, and audit outputs use historical metadata unless the user explicitly edits for rerun.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Edit mode restoration from task input snapshot
  - Rerun preservation of original command metadata and catalog versions
  - Edit-for-rerun warning recomputation without source-run mutation
  - Task details display of authored instructions plus command interpretation
  - Detection, render, and pass-through audit events
- Out of scope:
  - Initial submit parser behavior
  - Runtime renderer implementation details
  - Changing provider command semantics during rerun
- Acceptance criteria:
  - Edit mode restores authored instructions and runtimeCommand metadata from the task input snapshot when present.
  - If historical metadata is absent, edit mode may re-detect only for preview and must not alter the historical raw instruction value silently.
  - Exact rerun preserves original authored instructions, runtimeCommand metadata, runtimeCapabilityVersion, and hintCatalogVersion.
  - Edit-for-rerun may display recomputed warnings without mutating the original source run.
  - Task details show both original instructions and runtime command interpretation including command, runtime, render mode, and status when available.
  - Audit events record runtime_command.detected, runtime_command.rendered, and runtime_command.passthrough details without secrets.
- Requirements:
  - Store enough command metadata to explain historical recognition mode and catalog versions.
  - Use snapshot metadata as the source of truth for edit, rerun, task details, and audit views.
  - Emit observability events for detected, rendered, and opaque pass-through command cases.
  - Display original authored instructions alongside interpretation instead of replacing one with the other.
- Source design coverage:
  - `DESIGN-REQ-002` — Owns backend snapshot preservation of raw authored instructions during submit normalization.
  - `DESIGN-REQ-003` — Owns the RuntimeCommandInvocation fields and authoritative snapshot shape.
  - `DESIGN-REQ-014` — Owns historical restoration and rerun fidelity for authored text, command metadata, and catalog versions.
  - `DESIGN-REQ-015` — Owns emitted command events and task detail presentation of original instructions plus interpretation.
  - `DESIGN-REQ-018` — Owns the test suite coverage matrix for every parser, UI, backend, renderer, edit, and rerun scenario.
- Risks or open questions:
  - Rerun reconstruction may have multiple code paths that need a shared snapshot reader to avoid divergent behavior.
- Assumptions:
  - Existing task details and audit surfaces can display additional runtime command metadata without new persistent storage.

### STORY-005 — Enforce slash command security, non-goals, and cross-surface test coverage

- Short name: `slash-command-guardrails`
- Source reference: `docs/Steps/SlashCommands.md`
- Source sections: Unknown Command Contract, Security Requirements, Failure Modes, Testing Strategy, Acceptance Criteria, Non-goals
- Why: The design intentionally allows opaque provider commands; broad boundary tests are needed to prevent later allowlists, unsafe shell construction, or misplaced context from regressing the contract.
- Description: As a MoonMind maintainer, I want guardrail tests and policy assertions around slash-command behavior, so unknown commands, unsupported runtimes, materialization, security, and non-goals stay stable as providers add new command forms.
- Independent test: Run a focused slash-command regression suite containing every documented parser example and acceptance criterion, including security checks that command names are never used to form shell commands and unknown commands are not materialized.
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004
- Needs clarification: None
- Scope:
  - Regression coverage tying parser, Create page, backend, renderer, edit, and rerun examples together
  - Security assertions for untrusted command args and bodies
  - Policy coverage for unsupported runtimes and malformed inputs
  - Non-goal enforcement that unknown provider commands are not blocked or converted into MoonMind workflow actions
- Out of scope:
  - Adding new runtime commands as MoonMind workflow actions
  - Implementing provider-specific semantics for /review or /simplify
  - Replacing provider-native command systems
- Acceptance criteria:
  - Parser tests cover /review, /review with args, /simplify, leading-space non-detection, escaped slash, path-like malformed input, unknown valid command, provider-style opaque command line, and non-leading slash text.
  - Create page tests cover command chips, runtime changes, escaped input, unknown pass-through status, and instruction immutability.
  - Backend tests cover missing frontend metadata, rejected malformed metadata, snapshot preservation, unknown pass-through, and unsupported-runtime policy.
  - Runtime tests prove Codex and Claude prompt-prefix render outputs start with the command and that RAG, skill summaries, and runtime notes never precede it.
  - Security tests prove user-authored command names, args, and bodies are treated as untrusted text and materialized command files are allowlisted only for known commands.
  - Non-goal tests or assertions prevent hints from becoming a blocking allowlist and prevent slash commands from becoming MoonMind workflow actions unless explicitly registered that way.
- Requirements:
  - Collect design examples into reusable parser, UI, backend, renderer, edit, rerun, and security tests.
  - Assert policy-controlled failure behavior for renderer failures and unsupported/malformed commands.
  - Keep test evidence aligned with the final acceptance criteria from the source design.
- Source design coverage:
  - `DESIGN-REQ-005` — Owns pass-through treatment for unknown valid commands during detection and snapshot normalization.
  - `DESIGN-REQ-016` — Owns security assertions across backend normalization and renderer behavior.
  - `DESIGN-REQ-017` — Owns typed or policy-controlled failure outcomes for unsupported, malformed, unknown, renderer, and context-order cases.
  - `DESIGN-REQ-018` — Owns the test suite coverage matrix for every parser, UI, backend, renderer, edit, and rerun scenario.
  - `DESIGN-REQ-019` — Owns non-goal enforcement through acceptance criteria and explicit out-of-scope boundaries.
- Risks or open questions:
  - If treated as a late technical chore, this story could lose vertical value; it should be run as the hardening and acceptance-lock story before implementation is considered complete.
- Assumptions:
  - Downstream implementation will follow repository test taxonomy and use focused unit plus frontend tests, with integration coverage where runtime adapter boundaries are exercised.

## Coverage Matrix

- `DESIGN-REQ-001` → STORY-001, STORY-002
- `DESIGN-REQ-002` → STORY-001, STORY-004
- `DESIGN-REQ-003` → STORY-001, STORY-004
- `DESIGN-REQ-004` → STORY-001
- `DESIGN-REQ-005` → STORY-001, STORY-005
- `DESIGN-REQ-006` → STORY-001, STORY-003
- `DESIGN-REQ-007` → STORY-001, STORY-002
- `DESIGN-REQ-008` → STORY-001, STORY-002
- `DESIGN-REQ-009` → STORY-002
- `DESIGN-REQ-010` → STORY-001
- `DESIGN-REQ-011` → STORY-003
- `DESIGN-REQ-012` → STORY-003
- `DESIGN-REQ-013` → STORY-003
- `DESIGN-REQ-014` → STORY-004
- `DESIGN-REQ-015` → STORY-004
- `DESIGN-REQ-016` → STORY-003, STORY-005
- `DESIGN-REQ-017` → STORY-003, STORY-005
- `DESIGN-REQ-018` → STORY-002, STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-019` → STORY-001, STORY-002, STORY-003, STORY-005

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001
- `STORY-004` depends on: STORY-001
- `STORY-005` depends on: STORY-001, STORY-002, STORY-003, STORY-004

## Out Of Scope

- Creating spec.md files or specs/ directories during breakdown.
- Implementing slash-command behavior during this breakdown step.
- Creating Jira issues during this breakdown step.
- Replacing provider-native command systems or requiring MoonMind to know every provider command.
- Treating slash commands as MoonMind workflow actions unless explicitly registered that way in a future design.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Downstream Notes

- Recommended first story for `/speckit.specify`: `STORY-001` (`runtime-command-snapshot`).
- Stories with unresolved `[NEEDS CLARIFICATION]` markers: none.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Run `/speckit.verify` after implementation to compare final behavior against the original design preserved through specify.
