# Data Model: Runtime Command Rendering After Context Preparation

## Runtime Command Invocation

Represents the backend-normalized command metadata derived from authored instructions.

Fields:
- `kind`: fixed value for slash-command invocations.
- `source`: how the invocation was detected, such as leading slash.
- `sourcePath`: task field path where the authored command originated.
- `targetRuntime`: selected runtime ID at submit time.
- `targetStepId`: optional step identifier for step-level commands.
- `command`: normalized command token, empty for escaped or malformed literals.
- `rawCommand`: first authored command line exactly as preserved for audit.
- `args`: command arguments when the grammar supports them.
- `instructionBody`: authored body after the command line, or literal instruction body for escaped/malformed cases.
- `detectionStatus`: detected, escaped, malformed, or equivalent normalized status.
- `hintStatus`: hinted or opaque.
- `recognitionMode`: runtime pass-through, hinted pass-through, escaped literal, unsupported runtime, or equivalent mode.
- `requiresRuntimeRecognition`: whether the runtime must see a command in recognition position.
- `runtimeCapabilityVersion`: capability metadata version used when detected.
- `hintCatalogVersion`: hint metadata version used when detected.
- `detectionPhase`: submit for backend normalization.

Validation rules:
- Command names, args, and bodies are untrusted authored text.
- Unknown valid commands may be opaque but must remain valid for pass-through runtimes.
- Escaped and malformed commands must not require runtime recognition.
- Runtime launch must not trust frontend-supplied metadata without backend normalization.

## Prepared Runtime Context

Represents MoonMind-added context assembled after task submission and before process launch.

Fields:
- `instructionBody`: user-authored instruction body after command parsing.
- `retrievalContext`: optional retrieved context text or reference produced during workspace preparation.
- `skillActivationSummary`: optional active skill summary projected into runtime instructions.
- `managedRuntimeNotes`: runtime-specific operational notes added by MoonMind.
- `otherPreparedContext`: any future MoonMind-owned runtime prepended/appended context.

Validation rules:
- For prompt-prefix commands, prepared context must appear after command and instruction body.
- Prepared context must not be interpreted as trusted instructions when source wrappers classify it as retrieved or system-owned reference data.
- Empty context components must not alter command placement.

## Runtime Render Outcome

Represents the runtime strategy's final rendering decision before process launch or turn submission.

Fields:
- `status`: ok, unsupported, failed, or fallback.
- `renderMode`: plain_prompt, prompt_prefix, native_command, materialized_command, or unsupported.
- `renderedInstruction`: final runtime-visible instruction text when text transport is used.
- `nativeCommandPayload`: optional structured runtime command payload for runtimes with native command transport.
- `materializedTargets`: allowlisted files or artifacts produced by known-command materialization.
- `failureReason`: typed failure reason such as runtime_command_render_failed.
- `fallbackEvent`: auditable fallback metadata when policy permits literal fallback.
- `diagnostics`: redacted operator-facing render diagnostics.

Validation rules:
- Failure outcomes stop launch unless an explicit fallback event is present and policy permits it.
- Diagnostics must not expose secrets.
- Unknown commands cannot produce materialized targets.
- Prompt-prefix outcomes must start with the slash command for recognition-required invocations.

## Runtime Capability

Represents declarative runtime support for slash-command rendering.

Fields:
- `runtimeId`: canonical runtime identifier.
- `slashCommandPassthrough`: whether opaque slash commands can pass through.
- `supportedRenderModes`: render modes supported by this runtime.
- `materializedCommandAllowlist`: optional known-command materialization capabilities.
- `literalFallbackPolicy`: whether literal fallback is allowed for unsupported or failed rendering.
- `capabilityVersion`: version used for audit and rerun explanation.

Validation rules:
- Capability data is runtime-level, not a per-command blocking allowlist.
- Future runtimes can opt into supported modes without changing Create page behavior.
- Unsupported values fail explicitly rather than silently changing command semantics.

## State Transitions

```text
authored instructions
  -> backend-normalized Runtime Command Invocation
  -> workspace/context preparation mutates prepared context
  -> runtime strategy produces Runtime Render Outcome
  -> launcher starts process only when outcome is ok or approved fallback
  -> diagnostics/history preserve render mode, failures, and MM-686 traceability
```
