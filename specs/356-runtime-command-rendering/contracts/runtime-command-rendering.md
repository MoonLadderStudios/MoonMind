# Runtime Command Rendering Contract

## Scope

This contract defines the managed-runtime boundary for MM-686. It is consumed by runtime strategies and the launcher after task input normalization and after context preparation.

## Inputs

A runtime renderer receives:

```yaml
runtimeId: codex_cli | claude_code | <future-runtime>
rawInstructions: string
runtimeCommand: RuntimeCommandInvocation | null
instructionBody: string
preparedContext:
  retrievalContext: string | null
  skillActivationSummary: string | null
  managedRuntimeNotes: string | null
  otherPreparedContext: string | null
runtimeCapability:
  slashCommandPassthrough: boolean
  supportedRenderModes: [plain_prompt, prompt_prefix, native_command, materialized_command]
  materializedCommandAllowlist: [string]
  literalFallbackPolicy: reject | literal_prompt_allowed
```

Rules:
- `runtimeCommand` is backend-normalized metadata, not frontend-supplied truth.
- `command`, `args`, `rawCommand`, and `instructionBody` are untrusted authored text.
- `preparedContext` is MoonMind-owned launch context and must not precede command text that requires first-position recognition.

## Outcomes

The renderer returns one of the following outcomes:

```yaml
status: ok
renderMode: prompt_prefix
renderedInstruction: |
  /review

  <instruction body>

  <prepared context>
```

```yaml
status: ok
renderMode: plain_prompt
renderedInstruction: string
```

```yaml
status: ok
renderMode: native_command
nativeCommandPayload:
  command: string
  args: string
  instructionBody: string
  preparedContext: string
```

```yaml
status: ok
renderMode: materialized_command
renderedInstruction: string
materializedTargets:
  - path: string
    command: string
```

```yaml
status: failed
failureReason: runtime_command_render_failed
diagnostics:
  message: string
```

```yaml
status: fallback
renderMode: plain_prompt
fallbackEvent:
  reason: renderer_failed | unsupported_runtime
  fallbackMode: literal_prompt
renderedInstruction: string
```

## Required Behavior

- Prompt-prefix output for `/review` starts with `/review` as the first runtime-visible text.
- Prompt-prefix output appends instruction body before prepared context.
- Retrieval context, skill summaries, and runtime notes never appear before the slash command when first-position recognition is required.
- Unknown valid commands remain slash-leading for slash-capable runtimes.
- Unknown valid commands are never materialized into files.
- Escaped literal slash commands render as literal text through a non-command prefix, quote block, or runtime-specific literal wrapper.
- Renderer failures stop launch unless an explicit policy-approved fallback event is returned.
- Diagnostics and fallback events are redacted and do not expose secrets.

## Test Matrix

| Case | Runtime | Expected outcome |
| --- | --- | --- |
| `/review` with retrieval context | Codex CLI | prompt-prefix starts with `/review`; context follows body |
| `/review` with skill summary and managed notes | Codex CLI | command remains first; summaries and notes follow |
| `/review` with retrieval context | Claude Code | prompt-prefix starts with `/review`; context follows body |
| `/future-command` without hint | Codex CLI or Claude Code | opaque prompt-prefix/native pass-through; no warning due to missing hint |
| `\/review` escaped literal | Codex CLI or Claude Code | literal render; runtime does not execute `/review` |
| unknown command with materialized mode requested | Any | failed outcome before file write |
| renderer exception | Any | `runtime_command_render_failed` or approved fallback event before launch |
