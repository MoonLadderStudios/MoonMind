# Data Model: Define Step Type Authoring Model

## Step Draft

Represents a user-authored step before final runtime submission.

Fields:
- `instructions`: shared authoring instructions preserved across Step Type changes.
- `stepType`: exactly one of `skill`, `tool`, or `preset` in draft state.
- Skill-specific state: selected skill ID, skill arguments, required capabilities.
- Tool-specific state: tool ID, optional version, typed input object text.
- Preset-specific state: selected preset, preset input values, preview/application state.
- `stepTypeMessage`: transient user-visible feedback when incompatible type-specific state is discarded.

Validation rules:
- A draft has one selected Step Type at a time.
- Hidden incompatible type-specific state must not be submitted as active configuration.
- Shared instructions remain compatible across Step Type changes.

## Executable Step Payload

Represents a runtime-ready step.

Fields:
- `type`: executable Step Type, `tool` or `skill`.
- `tool`: required for Tool steps and incompatible with Skill payloads.
- `skill`: required for Skill steps and incompatible with non-skill Tool payloads.
- `source`: optional provenance metadata for preset-derived steps.

Validation rules:
- `preset`, `activity`, and arbitrary command-like values are not executable Step Types.
- Tool steps must not include Skill payloads.
- Skill steps must not include non-skill Tool payloads.

## Preset Expansion

Represents authoring-time expansion of a Preset step into executable steps.

Fields:
- preset identifier and version.
- preset input values.
- resulting Tool and/or Skill steps.
- provenance linking generated steps back to the preset.

Validation rules:
- Runtime submission must use expanded executable steps, not unresolved preset invocation.
