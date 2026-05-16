# Data Model: Preserve Slash Command Fidelity

## Task Input Snapshot

Purpose: Durable source of truth for historical task authoring state.

Fields:

- `objective.instructions`: original authored task-level instructions.
- `objective.runtimeCommand`: optional runtime command interpretation for task-level instructions.
- `steps[].instructions`: original authored step-level instructions.
- `steps[].runtimeCommand`: optional runtime command interpretation for step-level instructions.
- `runtime.mode`: runtime selected when the snapshot was built.
- `traceability.jiraIssueKey`: preserves `MM-687` where available.

Validation rules:

- Authored instructions and runtime command interpretation are distinct fields.
- Missing `runtimeCommand` does not imply the original instructions may be rewritten.
- Existing snapshot content is immutable historical evidence for exact rerun and task details.

## Runtime Command Interpretation

Purpose: Explain how authored slash-leading text was understood at submission or rendering time.

Fields:

- `kind`: expected `slash_command` for command metadata.
- `source` and `sourcePath`: where the command was detected.
- `targetStepId`: step identifier when interpretation belongs to a step.
- `command`, `rawCommand`, `args`, `instructionBody`: parsed command details.
- `targetRuntime`: runtime selected for interpretation.
- `detectionStatus`: detected, escaped, malformed, or equivalent status.
- `hintStatus`: hinted or opaque.
- `recognitionMode`: recognition behavior at submit time.
- `renderMode`: runtime rendering behavior when available.
- `requiresRuntimeRecognition`: whether runtime first-position recognition is required.
- `runtimeCapabilityVersion`: capability catalog version used at submit time.
- `hintCatalogVersion`: hint catalog version used at submit time.
- `detectionPhase`: phase that produced the interpretation.
- `status`: operator-facing interpretation status for task detail display.

Validation rules:

- Command names, args, and bodies are untrusted authored text.
- Runtime and hint catalog versions are preserved as historical metadata, not replaced silently by current catalog data.
- Render mode is optional until rendering has occurred.
- Unknown opaque pass-through commands remain auditable without requiring a known hint.

## Rerun Draft

Purpose: Editable or exact reconstruction of a historical task for rerun flows.

Fields:

- `taskInstructions`: restored authored instructions.
- `runtimeCommand`: restored task-level interpretation when present.
- `steps[].instructions`: restored step authored instructions.
- `steps[].runtimeCommand`: restored step interpretation when present.
- `warnings[]`: optional current warnings for edit-for-rerun or absent metadata.
- `recovery.kind`: exact_full_rerun or edited_full_retry.
- `recovery.sourceWorkflowId` and `recovery.sourceRunId`: source-run provenance.

Validation rules:

- Exact rerun preserves source-run instructions and command metadata exactly.
- Edit-for-rerun may show current warnings but must not mutate source-run evidence.
- Historical snapshots without runtime command metadata may trigger preview-only re-detection.

## Task Detail Command Summary

Purpose: Operator-facing display of authored text and command interpretation.

Fields:

- `originalInstructions`: original authored instructions.
- `runtimeCommand.command`: command token when available.
- `runtimeCommand.runtime`: runtime name or identifier when available.
- `runtimeCommand.renderMode`: render mode when available.
- `runtimeCommand.status`: detected, rendered, passed through, unsupported, failed, escaped, malformed, or missing metadata.
- `runtimeCommand.versionSummary`: runtime capability and hint catalog versions when available.

Validation rules:

- Original instructions remain visible even when command interpretation is present.
- Missing metadata is displayed as unavailable or historical, not fabricated.
- Display content must not expose secrets outside authorized authored text.

## Runtime Command Audit Event

Purpose: Non-secret operator audit evidence for command detection, rendering, and opaque pass-through.

Event types:

- `runtime_command.detected`
- `runtime_command.rendered`
- `runtime_command.passthrough`

Fields:

- `event`: event type.
- `sourcePath`: task or step instruction source.
- `runtimeId`: runtime identifier when available.
- `command`: command token when safe to show.
- `renderMode`: render mode when available.
- `hintStatus`: hinted or opaque when available.
- `recognitionMode`: recognition mode when available.
- `runtimeCapabilityVersion`: capability version when available.
- `hintCatalogVersion`: hint version when available.
- `status`: operator-facing status.

Validation rules:

- Events contain no raw secrets, credentials, tokens, cookies, private keys, or secret refs resolved to plaintext.
- Unknown commands use pass-through semantics instead of false failure.
- Event payloads are compact and suitable for existing observability/control-event surfaces.

## State Transitions

```text
Submitted task
  -> authoritative task input snapshot with authored instructions and optional runtimeCommand
  -> edit mode restores snapshot fields
  -> exact rerun preserves snapshot fields and source provenance
  -> edit-for-rerun creates editable copy with current warnings only
  -> task details display original instructions plus interpretation
  -> audit events record detected/rendered/pass-through command evidence
```
