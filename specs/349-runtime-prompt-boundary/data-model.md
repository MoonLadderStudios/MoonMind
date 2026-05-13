# Data Model: Runtime Prompt Boundary

## Task Intent

Purpose: Stable user objective and execution instructions that remain independent of runtime-specific payload formatting.

Fields:
- `instructions`: textual task or step intent.
- `runtime`: selected runtime mode or adapter selector.
- `steps`: optional logical execution steps with stable step identifiers.

Validation:
- Runtime selection must not alter the canonical task intent.
- Runtime-specific provider payloads must be derived at adapter boundaries, not stored as canonical task intent.

## Artifact Reference

Purpose: Durable pointer to an input artifact, including image artifacts, without embedding binary content in workflow history or prompt text.

Fields:
- `artifactId`: stable artifact identity.
- `rawInputRef`: artifact reference used when a runtime can consume raw refs.
- `derivedContextRef`: generated context reference used by text-first runtimes when available.
- `contentType`, `filename`, `sizeBytes`: bounded metadata for diagnostics and prompt context.

Validation:
- Inline bytes, base64 payloads, data URLs, and generated markdown are rejected from prepared input contracts.
- Missing artifact identifiers produce explicit diagnostics.

## Attachment Target Binding

Purpose: The canonical target relationship between an artifact and either the task objective or a logical step.

Fields:
- `targetKind`: `objective` or `step`.
- `stepRef`: required when `targetKind` is `step`; forbidden for objective attachments.
- `stepOrdinal`: optional diagnostic position for step-scoped attachments.

Validation:
- Supported target kinds are exactly `objective` and `step`.
- Step attachments require a stable step reference.
- Runtime adapters must consume selected target bindings and must not add new target kinds or targeting rules.

## Prepared Runtime Input

Purpose: Runtime-facing representation selected from task intent and attachment bindings.

Fields:
- `manifestRef`: compact manifest reference.
- `objectiveContextRefs`: generated context refs for objective attachments.
- `stepContextRefs`: generated context refs for current step attachments.
- `rawInputRefs`: raw artifact refs selected for the current runtime step.
- `inputRefs`: deduplicated union of context refs and raw refs for adapter consumption.

Validation:
- Text-first runtimes use generated context refs through the canonical input attachment contract.
- Multimodal runtimes may consume raw refs through adapters while preserving canonical target binding.
- Sibling step refs are excluded from the current step prepared input.

## State Transitions

1. `authored`: task intent includes attachment refs and target binding.
2. `prepared`: artifacts are materialized or referenced; target-aware context manifest is produced.
3. `selected`: current runtime step receives objective plus current-step prepared inputs only.
4. `adapted`: runtime adapter maps selected prepared input to provider-facing representation.
5. `diagnostic`: missing or invalid preparation state produces bounded failure evidence instead of silent attachment loss.
