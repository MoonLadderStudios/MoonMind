# Runtime Slash Commands on Create Task

Status: Desired State
Owners: MoonMind Engineering
Last Updated: 2026-05-13
Canonical for: Create Task runtime slash-command pass-through, command hint metadata, runtime command rendering boundaries
Related: `docs/UI/CreatePage.md`, `docs/Tasks/TaskArchitecture.md`, `docs/Steps/SkillSystem.md`, `docs/ManagedAgents/CodexCliManagedSessions.md`, `docs/ManagedAgents/ClaudeCodeManagedSessions.md`

---

## Purpose

This document defines how MoonMind lets users pass runtime slash commands such as `/review`, `/simplify`, or newly introduced provider commands from the Create Task page as part of normal task instructions, while still allowing MoonMind to add any runtime-specific markup required for Codex CLI, Claude Code, or future managed runtimes to recognize the command as an actual command.

The system preserves the authored instructions through MoonMind's task input snapshot contract, derives a structured command invocation when the first character is `/`, and renders that invocation through a runtime-specific adapter at execution time.

This design intentionally keeps command detection and rendering out of one-off Create page logic. The Create page remains a task composition surface, consistent with the existing Create page design principles that steps and capabilities should be schema/capability-driven rather than hard-coded per workflow or preset.

## Problem

Users expect to be able to write instructions like:

```text
/review
Check this branch for correctness, regressions, and missing tests.
```

or:

```text
/simplify
Refactor the implementation while preserving behavior.
```

MoonMind currently passes task instructions into managed runtime launch paths. Codex CLI appends `request.instruction_ref` as the positional prompt to `codex exec`, while Claude Code appends `request.instruction_ref` after `-p --dangerously-skip-permissions`.

However, runtime command recognition may depend on the final input shape. If MoonMind prepends skill activation summaries, retrieval context, managed runtime notes, or other wrappers before the user’s `/command`, the runtime may no longer see `/` as the first character.

MoonMind therefore needs a declarative command system that:

1. Detects a leading slash command from normal instructions.
2. Preserves the raw user-authored text.
3. Stores a structured command invocation in the task input snapshot.
4. Lets each runtime declare whether it supports opaque slash-command pass-through.
5. Keeps final command rendering as late as possible, after MoonMind has prepared context.
6. Supports edit, rerun, audit, and task details without losing exactness.

## Design Principles

### Preserve user intent exactly

The text authored by the user is not rewritten by slash-command detection. Existing Create/API submission normalization may trim surrounding whitespace, but command parsing must not replace, alias, or reinterpret the user's command token.

### Treat slash commands as structured task metadata

A leading slash command is not only prompt text. It is normalized into a structured `RuntimeCommandInvocation` so MoonMind can render it safely and correctly for different runtimes.

Unknown command tokens remain valid runtime command invocations when the selected runtime supports slash-command pass-through. MoonMind does not maintain a blocking allowlist of every command a provider runtime may add.

### Runtime adapters own command rendering

The Create page detects and previews the command, but it does not know how Codex CLI, Claude Code, or future runtime command markup works. Runtime-specific command rendering belongs in managed-runtime strategy code.

### Render at the last responsible moment

MoonMind should render the final command form after RAG injection, skill activation, runtime notes, and other instruction preparation have happened.

### Keep command behavior declarative

Runtime slash-command support and render modes should be defined in a runtime capability catalog, not scattered across React conditionals. Optional known-command hints may enrich autocomplete, labels, descriptions, and examples, but they must not prevent unknown commands from passing through.

### Preserve edit and rerun fidelity

Task editing and rerun reconstruction already depend on trustworthy task instruction reconstruction. The slash command model must round-trip through the same authoritative task input snapshot path.

## Goals

MoonMind must support:

1. Users typing `/review`, `/simplify`, or any provider/runtime slash command as the first characters of Create Task instructions.
2. Automatic detection when `/` is the first character of a task or step instruction.
3. Runtime-level validation of whether slash-command pass-through is available.
4. Runtime-specific command rendering for Codex CLI, Claude Code, and future managed runtimes.
5. Preservation of authored instructions through the task input snapshot contract.
6. Exact reconstruction for edit, rerun, task details, and audit surfaces.
7. Opaque pass-through for unknown commands when the selected runtime supports slash commands.
8. An escape hatch for users who want literal text beginning with `/`.

## Non-goals

This system does not:

1. Require MoonMind to know every slash command supported by every runtime.
2. Require the Create page to know provider-specific command markup.
3. Require all runtimes to support the same commands.
4. Replace runtime-native command systems.
5. Treat slash commands as MoonMind workflow actions unless a command is explicitly registered that way.
6. Infer command semantics from prompt text after runtime context has been added.
7. Block unknown slash commands for pass-through runtimes merely because MoonMind lacks a local hint entry.

## Terminology

### Raw instructions

The authored instruction text as preserved by MoonMind's task input snapshot contract.

Example:

```text
/review
Focus on race conditions and missing tests.
```

### Runtime command

A structured representation of a user-authored command. The command may be known to MoonMind for suggestion purposes, or it may be opaque and known only to the selected runtime.

Example:

```json
{
  "kind": "slash_command",
  "command": "review",
  "rawCommand": "/review"
}
```

### Instruction body

The user-authored text after the command line.

Example:

```text
Focus on race conditions and missing tests.
```

### Rendered runtime input

The final prompt, command event, materialized command file, or other runtime-specific input sent to Codex CLI, Claude Code, or another runtime.

## Domain Model

### RuntimeCommandInvocation

```ts
type RuntimeCommandInvocation = {
  kind: "slash_command";

  source: "leading_slash" | "explicit_ui";
  sourcePath: string;

  command: string;
  rawCommand: string;
  args: string;
  instructionBody: string;

  targetRuntime?: string;
  targetStepId?: string;

  detectionStatus: "detected" | "escaped" | "not_detected" | "malformed";
  hintStatus: "hinted" | "opaque";
  recognitionMode:
    | "runtime_passthrough"
    | "hinted_runtime_passthrough"
    | "escaped_literal"
    | "runtime_does_not_support_slash_commands";

  requiresRuntimeRecognition: boolean;
  runtimeCapabilityVersion?: string;
  hintCatalogVersion?: string;

  detectedAt: string;
};
```

Example:

```json
{
  "kind": "slash_command",
  "source": "leading_slash",
  "sourcePath": "objective.instructions",
  "command": "review",
  "rawCommand": "/review",
  "args": "",
  "instructionBody": "Focus on correctness, regressions, and missing tests.",
  "targetRuntime": "claude_code",
  "detectionStatus": "detected",
  "hintStatus": "hinted",
  "recognitionMode": "hinted_runtime_passthrough",
  "requiresRuntimeRecognition": true,
  "runtimeCapabilityVersion": "2026-05-13",
  "hintCatalogVersion": "2026-05-13",
  "detectedAt": "submit"
}
```

### RuntimeCommandRenderResult

```ts
type RuntimeCommandRenderResult = {
  runtimeId: string;
  command: string;

  mode:
    | "plain_prompt"
    | "prompt_prefix"
    | "native_command"
    | "materialized_command"
    | "unsupported";

  renderedInstructionRef?: string;
  commandEvent?: Record<string, unknown>;
  materializedFiles?: string[];

  warning?: string;
};
```

## Task Input Shape

The authoritative task input snapshot should include both authored instructions and derived runtime command metadata.

```json
{
  "objective": {
    "instructions": "/review\nFocus on correctness, regressions, and missing tests.",
    "runtimeCommand": {
      "kind": "slash_command",
      "source": "leading_slash",
      "sourcePath": "objective.instructions",
      "command": "review",
      "rawCommand": "/review",
      "args": "",
      "instructionBody": "Focus on correctness, regressions, and missing tests.",
      "targetRuntime": "claude_code",
      "detectionStatus": "detected",
      "hintStatus": "hinted",
      "recognitionMode": "hinted_runtime_passthrough",
      "requiresRuntimeRecognition": true,
      "runtimeCapabilityVersion": "2026-05-13",
      "hintCatalogVersion": "2026-05-13",
      "detectedAt": "submit"
    }
  },
  "runtime": {
    "mode": "claude_code"
  }
}
```

For multi-step drafts, the command may appear at the task level or step level:

```json
{
  "steps": [
    {
      "id": "step-1",
      "instructions": "/simplify\nReduce duplication in the auth flow.",
      "runtimeCommand": {
        "kind": "slash_command",
        "source": "leading_slash",
        "sourcePath": "steps[0].instructions",
        "command": "simplify",
        "rawCommand": "/simplify",
        "args": "",
        "instructionBody": "Reduce duplication in the auth flow.",
        "targetStepId": "step-1",
        "detectionStatus": "detected",
        "hintStatus": "hinted",
        "recognitionMode": "hinted_runtime_passthrough",
        "requiresRuntimeRecognition": true,
        "runtimeCapabilityVersion": "2026-05-13",
        "hintCatalogVersion": "2026-05-13",
        "detectedAt": "submit"
      }
    }
  ]
}
```

## Detection Rules

Slash command detection is intentionally conservative.

A command is detected only when:

1. The instruction string is non-empty.
2. The first character is `/`.
3. The first line is slash-leading and can be represented as a runtime command token plus optional arguments, or as an opaque slash-leading command line.
4. The selected runtime either supports slash-command pass-through or MoonMind can report that this runtime will treat the text literally.

The command token does not need to be known to MoonMind. A matching but unhinted token is an opaque runtime command.

Recommended parser grammar for commands MoonMind can split into `command` and `args`:

```regex
^\/([A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z0-9_-]+)?)(?:\s+(.*))?$
```

Provider runtimes may introduce command forms outside this grammar. If the first line starts with `/` but does not match the structured grammar, MoonMind should preserve the full first line as an opaque runtime command for pass-through runtimes unless the line is clearly ordinary path text such as `/src/app.ts is broken`. Implementations should bias toward pass-through for interactive agent runtimes and toward literal text only when the input is escaped, malformed as ordinary text, or the runtime lacks slash-command support.

Examples:

| Input                    | Detection result                        |
| ------------------------ | --------------------------------------- |
| `/review`                | command `review`                        |
| `/review focus on tests` | command `review`, args `focus on tests` |
| `/simplify`              | command `simplify`                      |
| ` /review`               | not detected                            |
| `\/review`               | escaped literal                         |
| `/src/app.ts is broken`  | malformed command, treated as text      |
| `/future-command now`    | opaque command `future-command`         |
| `/provider.command now`  | opaque command line                     |
| `Look at /review`        | not detected                            |

## Unknown Command Contract

Unknown valid slash commands are first-class runtime invocations for runtimes with `slashCommandPassthrough: true`.

Rules:

1. Unknown commands are not fallbacks.
2. Unknown commands are not warnings or errors merely because MoonMind lacks a hint.
3. Unknown commands must remain slash-leading in the final runtime-visible input.
4. Unknown commands must not be materialized into runtime command files.
5. Unknown commands must not be used to construct shell commands or runtime arguments other than the final user prompt, native opaque-command event, or equivalent adapter-owned transport.
6. Known-command hints may improve labels, descriptions, autocomplete, and examples, but absence from the hint catalog is never a rejection reason for pass-through runtimes.

## Escape Behavior

Users can force literal text by escaping the slash:

```text
\/review this should be treated as normal text
```

MoonMind stores:

```json
{
  "instructions": "/review this should be treated as normal text",
  "runtimeCommand": {
    "kind": "slash_command",
    "source": "leading_slash",
    "sourcePath": "objective.instructions",
    "command": "",
    "rawCommand": "\\/review",
    "args": "",
    "instructionBody": "/review this should be treated as normal text",
    "detectionStatus": "escaped",
    "hintStatus": "opaque",
    "recognitionMode": "escaped_literal",
    "requiresRuntimeRecognition": false,
    "detectedAt": "submit"
  }
}
```

The rendered runtime input is literal instructions. The renderer must not leave an unescaped slash command as the first runtime-visible text when the user escaped it to avoid command execution.

Recommended rendering:

```text
Literal text, not a runtime command:
/review this should be treated as normal text
```

The user-facing snapshot may store the literal body without the escape character, but runtime rendering must add a non-command prefix, quote block, or equivalent runtime-specific literal wrapper so the runtime does not execute `/review`.

## Runtime Capabilities and Command Hints

MoonMind should maintain a declarative runtime capability catalog. Capabilities are runtime-level, not a per-command allowlist.

```yaml
runtimes:
  codex_cli:
    slashCommandPassthrough: true
    renderMode: prompt_prefix
    commandHintsRef: codex_cli
  claude_code:
    slashCommandPassthrough: true
    renderMode: prompt_prefix
    commandHintsRef: claude_code
  some_runtime_without_slash_commands:
    slashCommandPassthrough: false
    renderMode: plain_prompt
```

MoonMind may also maintain optional known-command hints for suggestions, autocomplete, descriptions, examples, or policy text. Hints are not validation gates.

```yaml
knownRuntimeCommandHints:
  review:
    label: Review
    aliases:
      - /review
    description: Ask the selected runtime to review the current task or code state.
    argumentPolicy:
      allowed: true
      required: false
    bodyPolicy:
      allowed: true
      required: false

  simplify:
    label: Simplify
    aliases:
      - /simplify
    description: Ask the selected runtime to simplify the implementation.
    argumentPolicy:
      allowed: true
      required: false
    bodyPolicy:
      allowed: true
      required: false
```

The catalog may later support explicit runtime-specific materialization for commands MoonMind intentionally owns or enriches:

```yaml
knownRuntimeCommandHints:
  review:
    materialization:
      claude_code:
        renderMode: materialized_command
        materializedCommand:
          scope: project
          commandName: review
          invocation: /project:review
          templateRef: moonmind.review
```

Unknown opaque commands must not use materialized command mode. They pass through only through render modes that preserve user text as runtime input.

## Runtime Render Modes

### plain_prompt

The command is not treated as a runtime command. MoonMind sends literal instructions as normal text.

Used when:

1. The slash was escaped.
2. The selected runtime does not support slash-command pass-through and policy permits literal submission.
3. The first line is malformed and therefore is not a valid slash-command invocation.

This mode is not the fallback for unknown valid commands on runtimes that support slash-command pass-through. Unknown valid commands should remain slash-leading and pass through.

### prompt_prefix

MoonMind renders the slash command as the first runtime-visible text. This mode supports both known-command hints and opaque unknown commands.

Example:

```text
/review

Focus on correctness, regressions, and missing tests.

<MoonMind-prepared context follows here>
```

This mode is useful when the runtime recognizes slash commands based on the prompt text beginning with the slash command.

### native_command

MoonMind sends the command through a structured runtime/session protocol rather than prompt text.

Example:

```json
{
  "type": "runtime.command",
  "command": "review",
  "body": "Focus on correctness, regressions, and missing tests."
}
```

This is the preferred future mode for runtimes with session control planes. The Codex managed-session design already describes a normalized control/action vocabulary around session and turn lifecycle, which is a better long-term home for structured command events than prompt rewriting.

Unknown opaque commands may use native command mode only if the runtime adapter exposes a generic opaque-command transport. Otherwise they use `prompt_prefix`.

### materialized_command

MoonMind writes a runtime-specific command file or markup artifact, then invokes it.

Example:

```text
/project:review

Focus on correctness, regressions, and missing tests.
```

The exact materialization format is runtime-owned. Materialization requires explicit known-command hint metadata and an allowlisted renderer; opaque unknown commands are not materialized.

## Create Page Behavior

### Default authoring behavior

The task instruction field continues to accept ordinary text.

When the first character is `/`, the Create page parses the first line and checks the selected runtime's slash-command capability.

If the selected runtime supports slash-command pass-through, show:

```text
Runtime command: /review
This will be sent to Claude Code/Codex as a runtime slash command.
```

If the command also has a known-command hint, the Create page may show the hint:

```text
/review
Review the current code or task output.
```

If the command has no hint, do not warn merely because it is unknown to MoonMind:

```text
Runtime command: /future-command
MoonMind will pass this command through to the selected runtime.
```

If the selected runtime does not support slash-command pass-through, show:

```text
/review may not be recognized by the selected runtime. Choose a slash-command capable runtime or escape it as \/review to send literal text.
```

### Runtime changes

When the user changes the selected runtime, the Create page recomputes `recognitionMode`.

The authored instructions do not change.

### Edit mode

When loading an existing task in edit mode:

1. Authored instructions are restored from the task input snapshot.
2. Runtime command metadata is restored from the task input snapshot when present.
3. If metadata is absent, the Create page may re-detect the command for preview only.
4. Re-detected metadata must not silently alter the historical raw instruction value.

### Rerun mode

Rerun uses the original authored instructions and original command metadata.

If the runtime capability catalog or known-command hints have changed since the original run, MoonMind may display a warning, but exact rerun should preserve the original task configuration unless the user chooses edit-for-rerun. Persisted command metadata should include the runtime capability version and hint catalog version used at submit time so audits can explain whether a rerun is using the same pass-through assumptions or merely the same authored text.

## Backend Behavior

### Submit-time normalization

On task submit, the backend validates and canonicalizes runtime command metadata.

The frontend may provide a parsed preview, but backend normalization is authoritative.

Submit flow:

```text
1. Receive task draft.
2. Extract task-level and step-level instruction fields.
3. Detect leading slash commands.
4. Validate runtime-level slash-command capability and command grammar.
5. Store authored instructions according to the task input snapshot contract.
6. Store RuntimeCommandInvocation metadata.
7. Build authoritative task input snapshot.
8. Submit workflow.
```

### Server-side parser

The parser should expose:

```python
def parse_runtime_command(
    *,
    raw_instructions: str,
    source_path: str,
    target_runtime: str | None,
    runtime_capabilities: RuntimeCommandCapabilities,
    command_hints: RuntimeCommandHintCatalog | None = None,
) -> RuntimeCommandInvocation | None:
    ...
```

### Validation policy

MoonMind should support runtime-level policies:

```yaml
runtimeCommandPolicy:
  slashPassthroughRuntime: pass_through
  runtimeWithoutSlashPassthrough: warn_literal | reject
  escapedCommand: allow_plain_text
  malformedCommand: warn_literal | reject
```

Recommended default:

```yaml
runtimeCommandPolicy:
  slashPassthroughRuntime: pass_through
  runtimeWithoutSlashPassthrough: warn_literal
  escapedCommand: allow_plain_text
  malformedCommand: warn_literal
```

Unknown valid commands are not rejected by policy when `slashPassthroughRuntime` is `pass_through`. For enterprise or tightly managed environments, `reject` may be used only for runtimes that do not support slash pass-through or for malformed command lines.

## Runtime Preparation Pipeline

Runtime command rendering must happen after MoonMind has prepared the context, but before the command is passed to the runtime.

Desired order:

```text
raw task input
  -> parse runtime command
  -> build task input snapshot
  -> workflow creates AgentExecutionRequest
  -> runtime strategy prepares workspace
  -> RAG/context injection mutates instruction body
  -> skill activation summary is resolved
  -> managed runtime notes are prepared
  -> runtime command renderer produces final runtime input
  -> runtime strategy launches process or sends turn
```

This avoids losing command recognition when MoonMind adds context.

Implementation note: the current managed runtime path mutates `request.instruction_ref` during retrieval injection, skill activation summary projection, and managed runtime note preparation. Slash-command support therefore requires an explicit final render boundary after those mutations. Parser-only changes are insufficient because they would still allow prepared context to move the slash command away from the first runtime-visible position.

## Runtime Strategy Integration

### Interface

```python
class RuntimeCommandRenderer:
    runtime_id: str

    def supports_slash_passthrough(
        self,
    ) -> bool:
        ...

    async def materialize_command(
        self,
        *,
        workspace_path: Path | None,
        command: RuntimeCommandInvocation,
        environment: Mapping[str, str] | None,
    ) -> list[str]:
        ...

    def render_instruction(
        self,
        *,
        command: RuntimeCommandInvocation | None,
        instruction_body: str,
        prepared_context: str,
        raw_instructions: str,
    ) -> RuntimeCommandRenderResult:
        ...
```

### Codex CLI rendering

Codex CLI currently builds a command from the runtime profile and appends `request.instruction_ref` as the positional prompt.

For `prompt_prefix` mode, the renderer produces:

```text
/review

<instruction body>

<MoonMind prepared context and managed runtime notes>
```

The slash command remains the first runtime-visible characters.

This applies to opaque unknown commands as well as commands with MoonMind hint metadata.

### Claude Code rendering

Claude Code currently builds a command with `-p --dangerously-skip-permissions` and appends `request.instruction_ref`.

For `prompt_prefix` mode, the renderer produces:

```text
/review

<instruction body>

<MoonMind prepared context>
```

For `materialized_command` mode, the renderer may produce:

```text
/project:review

<instruction body>

<MoonMind prepared context>
```

and materialize any runtime-specific command files before launch.

The Create page does not know which mode Claude uses.

## Audit and Observability

Every run with a runtime command should record:

```json
{
  "event": "runtime_command.detected",
  "runtimeId": "claude_code",
  "command": "review",
  "sourcePath": "objective.instructions",
  "hintStatus": "hinted",
  "recognitionMode": "hinted_runtime_passthrough",
  "runtimeCapabilityVersion": "2026-05-13",
  "hintCatalogVersion": "2026-05-13"
}
```

At render time:

```json
{
  "event": "runtime_command.rendered",
  "runtimeId": "claude_code",
  "command": "review",
  "renderMode": "prompt_prefix"
}
```

For opaque unknown commands, observability should record pass-through rather than fallback:

```json
{
  "event": "runtime_command.passthrough",
  "runtimeId": "claude_code",
  "command": "foo",
  "hintStatus": "opaque",
  "renderMode": "prompt_prefix"
}
```

Task details should show both:

1. Original authored instructions.
2. Runtime command interpretation.

Example:

```text
Original instructions

/review
Focus on correctness, regressions, and missing tests.

Runtime command

Command: /review
Runtime: Claude Code
Render mode: prompt prefix
Status: passed through
```

## Security Requirements

1. Runtime capability and known-command hint data are declarative and trusted by MoonMind.
2. User-authored command names are never used to construct shell commands directly.
3. Materialized command files are written only through allowlisted renderers.
4. Unknown commands do not trigger materialization.
5. Runtime command rendering must not expose secrets.
6. Authored instructions remain auditable.
7. Backend validation is authoritative.
8. Runtime renderers must treat user command args and body as untrusted text.
9. Slash-command pass-through may be disabled by policy per runtime, workspace, tenant, or provider profile.
10. Known-command hints must never become a blocking allowlist for pass-through runtimes.

## Failure Modes

### Unknown command

Input:

```text
/foo
Do something.
```

Default behavior:

```text
No warning is required merely because MoonMind lacks a hint.
Stored as authored instructions and command metadata.
runtimeCommand.hintStatus = "opaque".
runtimeCommand.recognitionMode = "runtime_passthrough".
Rendered with the command in the runtime-required recognition position.
```

### Runtime without slash pass-through

Input:

```text
/review
Check this.
```

Selected runtime:

```text
some_runtime_without_slash_commands
```

Default behavior:

```text
Warning shown.
Stored as authored instructions and command metadata.
Rendered as literal text unless strict policy rejects.
```

### Renderer failure

If runtime-specific materialization fails, the task should fail before launching the agent with:

```json
{
  "failureClass": "user_error",
  "reason": "runtime_command_render_failed"
}
```

or, if policy permits fallback:

```json
{
  "event": "runtime_command.fallback",
  "reason": "renderer_failed",
  "fallbackMode": "literal_prompt"
}
```

### Context preparation moves the slash

This must not happen. The renderer is responsible for producing final runtime input with the command in the correct runtime-required position.

## Testing Strategy

### Parser tests

```text
/review
  -> command=review

/review focus on tests
  -> command=review
  -> args="focus on tests"

/simplify
  -> command=simplify

 /review
  -> no command

\/review
  -> escaped literal

/src/foo.ts
  -> malformed command, literal text

/future-command
  -> opaque command=future-command
```

### Create page tests

1. Leading `/review` shows a runtime command chip.
2. Changing runtime recomputes recognition mode.
3. Escaped `\/review` does not show command chip.
4. Unknown valid `/foo` shows pass-through status, not a blocking warning.
5. Authored instructions remain unchanged after detection.

### Backend tests

1. Backend detects command even if frontend metadata is missing.
2. Backend rejects malformed frontend command metadata.
3. Backend preserves authored instructions according to the task input snapshot contract.
4. Backend stores command metadata in the authoritative task input snapshot.
5. Unknown valid commands pass through for slash-capable runtimes.
6. Runtimes without slash pass-through follow configured policy.

### Runtime rendering tests

1. Codex prompt-prefix render starts with `/review`.
2. Claude prompt-prefix render starts with `/review`.
3. Unknown valid `/future-command` prompt-prefix render starts with `/future-command`.
4. Skill activation summaries do not appear before the command.
5. RAG context does not appear before the command.
6. Managed runtime notes do not appear before the command.
7. Materialized command mode writes only allowlisted files for known commands.
8. Renderer failure produces a typed failure or policy-approved fallback.

### Edit and rerun tests

1. Edit mode restores authored instructions from the task input snapshot.
2. Edit mode restores command metadata from the task input snapshot.
3. Rerun preserves original command metadata.
4. Edit-for-rerun can recompute recognition warnings without altering the source run.
5. Task details show authored instructions and rendered command metadata.

## Acceptance Criteria

The system is correct when all of the following are true:

1. A user can enter `/review` as the first characters of Create Task instructions.
2. MoonMind detects `/review` as a structured runtime command.
3. MoonMind preserves authored instructions through the authoritative task input snapshot.
4. MoonMind stores command metadata in the authoritative task input snapshot.
5. The Create page displays whether the selected runtime supports slash-command pass-through.
6. Unknown valid commands pass through for runtimes with slash-command pass-through.
7. Users can escape slash-leading text with `\/`.
8. Runtime-specific renderers decide how to make the command recognized by Codex CLI or Claude Code.
9. MoonMind-added context never accidentally moves the command out of the runtime-required recognition position.
10. Edit, rerun, task details, and audit surfaces can show both the original instructions and the interpreted runtime command.
11. No Codex-specific or Claude-specific command markup is hard-coded into the Create page.
12. Future runtimes can add slash-command support by registering runtime capabilities and implementing a renderer.
13. Future suggestion features can add known-command hints without blocking commands absent from the hint catalog.
