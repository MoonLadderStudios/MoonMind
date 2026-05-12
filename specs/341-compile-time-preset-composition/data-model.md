# Data Model: Compile-Time Preset Composition With Provenance Preservation

## Task Draft

Authoring-state task before submission.

Key fields:
- Manual steps authored directly by the user.
- Selected preset bindings and nested include metadata.
- Runtime, publish intent, Jira provenance, and attachment references.

Validation rules:
- Preset include references must resolve before execution finalization.
- Manual-only drafts must not gain preset metadata.

## Preset Include Tree

Nested preset composition graph selected during task authoring.

Key fields:
- Preset identifier or slug.
- Version.
- Alias.
- Include path.
- Input mapping.
- Original step identifiers.

Validation rules:
- Reject cycles, missing references, disabled or unauthorized presets, version mismatches, conflicting aliases, and incompatible mappings.
- The tree resolves to bounded, deterministic flattened steps.

## Compiled Task Snapshot

Submitted task representation used for execution, audit, rerun, and reconstruction.

Key fields:
- Final ordered executable steps.
- `authoredPresets` bindings.
- `appliedStepTemplates` composition summary.
- Per-step `source` provenance.
- Existing runtime, publish, Jira, and attachment metadata.

Validation rules:
- Must not require live preset catalog lookup to reconstruct submitted work.
- Must preserve compact provenance rather than full template content.

## Step Source Provenance

Per-step origin metadata.

Key fields:
- `kind`.
- `presetId` or `presetSlug`.
- `version`.
- `includePath`.
- `originalStepId`.
- `inputMapping`.
- Detachment state when present.

Validation rules:
- Present only when reliable source data exists.
- Detached steps preserve enough source data for audit while reflecting detachment.

## Worker-Facing Payload

Execution payload consumed by managed workers.

Key fields:
- Resolved executable steps.
- Compact task and preset provenance metadata.

Validation rules:
- Must not contain unresolved preset include work as executable steps.
- Must not require worker-side preset expansion.
