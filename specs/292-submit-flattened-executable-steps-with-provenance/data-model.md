# Data Model: Submit Flattened Executable Steps with Provenance

## ExecutableStep

Represents a submitted task step that can be executed without resolving a preset catalog entry.

Fields:
- `id`: optional stable step identity.
- `title`: optional display label.
- `instructions`: optional step-level instructions.
- `type`: executable Step Type, either `tool` or `skill`.
- `tool`: typed operation payload when `type` is `tool`.
- `skill`: agent-facing behavior payload when `type` is `skill`.
- `source`: optional `PresetProvenance` metadata when the step was generated from a preset.

Validation rules:
- `type` must be `tool` or `skill` at executable submission and promotion boundaries.
- `type: "preset"` is rejected by default because Preset is an authoring-time placeholder.
- A Tool step must carry a Tool payload and must not carry a Skill payload.
- A Skill step must carry or resolve to Skill behavior and must not carry a non-Skill Tool payload.

## PresetProvenance

Metadata attached to an executable step generated from a preset.

Fields:
- `kind`: `preset-derived` for generated steps, with existing manual/detached values remaining non-runtime metadata.
- `presetId`: identifier for the source preset.
- `presetVersion`: version of the source preset used for expansion.
- `includePath`: ordered include path when the step came from a nested include.
- `originalStepId`: identifier of the source preset step.

Validation rules:
- Provenance is optional for runtime execution.
- Preset application, review, and proposal surfaces preserve `kind`, `presetId`, `presetVersion`, and `originalStepId` when the expansion source provides those values.
- `includePath` is preserved when the expansion source provides an include path.
- Missing, stale, or partial provenance must not make an otherwise valid executable Tool or Skill step require live preset lookup.
- Provenance must not control runtime materialization.
- Provenance may be used for audit, review, grouping, proposal reconstruction, and explicit refresh workflows.

## FlatExecutablePayload

Reviewed task or proposal payload ready for execution.

Fields:
- `instructions`: task-level instructions.
- `steps`: ordered list of `ExecutableStep` entries.
- `authoredPresets`: optional audit/reconstruction binding metadata for applied presets.

Validation rules:
- The payload must not contain unresolved Preset steps by default.
- The payload remains executable when provenance is stale or live preset catalog access is unavailable.
- The payload is the source of truth for proposal promotion unless the operator explicitly refreshes it through preview and validation.

## PromotableProposal

Stored proposal that can be promoted into a task execution.

Fields:
- `taskCreateRequest`: reviewed task creation envelope containing a `FlatExecutablePayload`.
- `status`: proposal lifecycle state.
- `review metadata`: priority, origin, decision note, and related proposal metadata.

Validation rules:
- Promotion validates the stored flat executable payload before execution.
- Promotion rejects unresolved Preset steps.
- Promotion preserves reviewed steps and provenance metadata.
- Promotion does not silently re-expand live preset catalog entries.

## State Transitions

1. `Preset draft step` -> explicit preview request.
2. `Preset preview` -> generated executable Tool/Skill steps plus warnings or validation errors.
3. `Applied preset` -> `FlatExecutablePayload` with executable steps and provenance metadata.
4. `FlatExecutablePayload` -> runtime plan or promoted execution after validation.
5. `Refresh requested` -> preview and validation before replacing the stored flat payload.
