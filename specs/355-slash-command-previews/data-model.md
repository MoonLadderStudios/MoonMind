# Data Model: Provider-Neutral Slash Command Previews

## RuntimeCommandPreview

Represents the Create page's user-visible interpretation of authored instructions.

Fields:

- `sourcePath`: `objective.instructions` or `steps[index].instructions`.
- `rawInstructions`: current authored instruction text used for preview derivation.
- `rawCommand`: first slash-leading line when applicable.
- `command`: parsed command token when available.
- `args`: command arguments when available.
- `instructionBody`: authored body after the command line when applicable.
- `detectionStatus`: `detected`, `escaped`, `not_detected`, or `malformed`.
- `hintStatus`: `hinted` or `opaque`.
- `recognitionMode`: `hinted_runtime_passthrough`, `runtime_passthrough`, `escaped_literal`, `runtime_does_not_support_slash_commands`, or `not_detected`.
- `requiresRuntimeRecognition`: true when the selected runtime is expected to interpret the command.
- `messageSeverity`: `info`, `warning`, or `neutral`.
- `label`: user-facing concise preview label.
- `description`: user-facing explanation.
- `source`: `derived` for local preview derivation or `snapshot` for restored edit-mode metadata.

Validation rules:

- Leading whitespace before `/` yields `not_detected`.
- `\/` yields `escaped` and `escaped_literal`.
- Path-like slash-leading text yields `malformed` and literal/warning presentation.
- Unknown valid commands are `opaque` but not warnings when runtime pass-through is supported.
- Preview derivation must not mutate `rawInstructions`.

## RuntimeCommandCapability

Browser-safe runtime metadata for preview behavior.

Fields:

- `runtimeId`: runtime identifier used by task submission.
- `slashCommandPassthrough`: whether slash-leading commands can pass through to the runtime.
- `renderMode`: preview-safe render mode label, used for explanation only.
- `commandHintsRef`: optional hint catalog identifier.
- `capabilityVersion`: version string for audit and drift explanations.

Validation rules:

- Unknown runtimes default to no slash-command pass-through unless explicitly configured.
- Capability values are declarative metadata, not provider-specific UI branches.

## RuntimeCommandHint

Optional metadata for known command previews.

Fields:

- `command`: canonical command token without the leading slash.
- `aliases`: slash-leading display aliases.
- `label`: concise human-readable command label.
- `description`: user-facing hint text.
- `argumentPolicy`: whether arguments are allowed or required.
- `bodyPolicy`: whether body text is allowed or required.
- `hintCatalogVersion`: version string.

Validation rules:

- Missing hints must not block preview or submission for slash-capable runtimes.
- Hints may enrich labels and descriptions only.

## AuthoredInstructionPreviewBinding

Links preview state to the instruction control that produced it.

Fields:

- `targetKind`: `objective` or `step`.
- `stepId`: step local or persisted identifier when target is a step.
- `stepOrdinal`: zero-based step order when target is a step.
- `instructions`: current authored instructions.
- `storedRuntimeCommand`: optional metadata restored from a task input snapshot.
- `preview`: current `RuntimeCommandPreview`.

State transitions:

- `empty` -> `not_detected` when text does not begin with `/`.
- `not_detected` -> `detected` when the first character becomes `/` and command syntax is valid or opaque.
- `detected` -> `runtime_does_not_support_slash_commands` when runtime changes to one without pass-through.
- `detected` -> `escaped` when the first characters become `\/`.
- `snapshot` -> `derived` only when stored metadata is absent or no longer matches the current authored text.
