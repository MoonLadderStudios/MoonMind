# Data Model: Composable Preset Expansion

## Preset Include

Represents a compositional entry inside an existing preset version `steps` JSON array.

Fields:
- `kind`: Required literal `include`.
- `slug`: Required child preset slug.
- `version`: Required child preset version label. Must be pinned.
- `alias`: Required alias for this include instance. Repeated child includes in the same parent must use distinct aliases.
- `scope`: Optional child scope. Defaults to the parent scope. `global` and `personal` are valid values.
- `inputMapping`: Optional object mapping child input names to literal or rendered values.

Validation rules:
- Global parent presets cannot include personal child presets.
- Missing, unreadable, inactive, or input-incompatible child versions are rejected.
- Child step overrides are not supported.

## Expansion Tree

Represents the resolved include graph for one expansion request.

Fields:
- `slug`
- `version`
- `scope`
- `alias` when the node is reached by include
- `path`: Ordered include path from root to node
- `includes`: Child expansion tree nodes
- `stepIds`: Flattened step IDs emitted from this node

Validation rules:
- A `(scope, slug, version)` already present in the current path is a cycle.
- Error messages for cycles and limit failures include the path.

## Flattened Plan

Represents the concrete ordered steps returned by expansion and consumed by existing downstream boundaries.

Fields:
- Existing step fields such as `id`, `title`, `instructions`, `skill`, and annotations.
- `presetProvenance`: Source metadata for audit and save-as-preset reasoning.

Validation rules:
- Step IDs remain deterministic for the root preset, selected version, flattened index, and root input set.
- The root preset `max_step_count` applies after include flattening.

## Preset Provenance

Metadata attached to each flattened step.

Fields:
- `root`: Root preset slug and version.
- `source`: Source preset slug, version, concrete source step index, and scope.
- `path`: Include path from root to the source step.
- `alias`: Include alias for child-provided steps, absent for root concrete steps.

Validation rules:
- Provenance must be present on steps produced through the composable expansion path.
- Provenance is compact metadata, not embedded large skill or preset content.

## Detachment

Save-as-preset classification for a selected group of expanded steps.

States:
- `intact_include`: Selection exactly matches a provenance subtree and can be serialized as an include.
- `detached_steps`: Selection is customized, partial, or provenance-mismatched and must be serialized as concrete steps.

Validation rules:
- Exact-match preservation is allowed only when source provenance remains unchanged.
- Detached or custom steps never preserve include semantics silently.
