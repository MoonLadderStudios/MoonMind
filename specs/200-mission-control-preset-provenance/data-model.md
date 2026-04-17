# Data Model: Mission Control Preset Provenance Surfaces

## Preset Provenance

- **Purpose**: Explain whether a task or step originated manually, from a preset, or from a preset include path.
- **Fields**:
  - `kind`: Manual, Preset, or Preset path.
  - `label`: Operator-facing compact label.
  - `presetPath`: Optional include path or preset lineage.
  - `detached`: Whether the step no longer follows the preset binding.
- **Validation Rules**:
  - Provenance is explanatory metadata and does not alter execution ordering.
  - Unknown or unavailable provenance must degrade to flat step presentation.

## Composed Preset Preview

- **Purpose**: Explain preset-derived draft content before submission.
- **Fields**:
  - `bindings`: Authored preset bindings included in the draft.
  - `flatSteps`: Resolved step order intended for submission.
  - `unresolvedIncludes`: Any includes that failed to resolve.
- **Validation Rules**:
  - Unresolved includes must be blocked before runtime submission.
  - Preview grouping must not replace flat submitted step order.

## Expansion Summary

- **Purpose**: Secondary evidence describing preset expansion.
- **Fields**:
  - `includeTree`: Optional tree summary of included presets.
  - `stepMappings`: Optional mapping from flat steps to preset paths.
  - `generatedAt`: Summary generation timestamp when available.
- **Validation Rules**:
  - Expansion summaries are secondary to flat steps, logs, diagnostics, and output artifacts.
  - Missing or stale expansion summaries must not block execution evidence review.
