# Data Model: Normalize Slash-Leading Instructions

Source traceability: MM-684 canonical Jira preset brief.

## RuntimeCommandInvocation

Represents command metadata derived from one authored instruction field.

Fields:
- `kind`: constant `slash_command`.
- `source`: `leading_slash` for detected slash commands, `explicit_ui` only for future explicit UI command sources.
- `sourcePath`: canonical path to the instruction field, such as `objective.instructions` or `steps[0].instructions`.
- `command`: parsed command token without the leading slash; empty for escaped literals or malformed literal handling.
- `rawCommand`: original first-line command token or escaped marker as authored after existing surrounding-whitespace normalization.
- `args`: parsed argument text from the first command line, or empty string.
- `instructionBody`: authored text after the first command line, or literal body for escaped commands.
- `targetRuntime`: normalized runtime mode when available.
- `targetStepId`: step id for step-level commands.
- `detectionStatus`: `detected`, `escaped`, `not_detected`, or `malformed`.
- `hintStatus`: `hinted` or `opaque`.
- `recognitionMode`: `runtime_passthrough`, `hinted_runtime_passthrough`, `escaped_literal`, or `runtime_does_not_support_slash_commands`.
- `requiresRuntimeRecognition`: whether a runtime adapter must preserve command recognition.
- `runtimeCapabilityVersion`: version marker for the runtime capability catalog used during normalization.
- `hintCatalogVersion`: version marker when a known-command hint catalog was consulted.
- `detectionPhase`: `submit`.

Validation rules:
- `sourcePath` is required for every metadata object.
- `targetStepId` is required for step-level instruction metadata.
- Unknown valid commands are allowed and use `hintStatus=opaque`.
- Escaped literals use `detectionStatus=escaped`, `recognitionMode=escaped_literal`, and `requiresRuntimeRecognition=false`.
- Unsupported runtimes or malformed ordinary path-like inputs must not produce executable pass-through recognition metadata.

## RuntimeCommandPolicy

Represents default validation outcomes for slash command normalization.

Fields:
- `slashPassthroughRuntime`: default `pass_through`.
- `runtimeWithoutSlashPassthrough`: default `warn_literal`.
- `escapedCommand`: default `allow_plain_text`.
- `malformedCommand`: default `warn_literal`.

Validation rules:
- Valid unknown commands are never rejected solely because no hint exists.
- Unsupported-runtime and malformed command handling may warn or reject according to policy, but accepted snapshots preserve authored text.

## AuthoritativeTaskInputSnapshot

Existing durable snapshot extended with optional runtime command metadata.

Relevant fields:
- `objective.instructions`: preserved task-level authored instructions.
- `objective.runtimeCommand`: optional `RuntimeCommandInvocation` for task-level instruction metadata.
- `steps[].instructions`: preserved step-level authored instructions.
- `steps[].runtimeCommand`: optional `RuntimeCommandInvocation` for step-level instruction metadata.
- `runtime.mode`: normalized or supplied target runtime used for capability classification.

State transitions:
- No leading slash: no runtime command metadata.
- Leading slash valid and runtime supports pass-through: detected metadata requiring runtime recognition.
- Leading slash unknown but valid: detected opaque metadata requiring runtime recognition.
- Escaped slash: escaped literal metadata not requiring runtime recognition.
- Malformed/path-like or unsupported runtime: non-executable metadata or warning state according to policy.
