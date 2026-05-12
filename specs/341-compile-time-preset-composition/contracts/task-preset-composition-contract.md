# Contract: Task Preset Composition

## Boundary

The control plane compiles task presets before execution submission is finalized. Workers consume the resolved task payload and do not expand presets or read the live preset catalog for already submitted work.

## Inputs

A task-shaped submission may contain:
- Manual task steps.
- Selected preset or task template metadata.
- Recursive include metadata.
- Existing runtime, publish, Jira, and attachment fields.

## Outputs

A successful submitted task payload contains:
- A deterministic final `steps` array containing executable work.
- `steps[].source` for preset-derived, included, or detached steps when reliable origin data exists.
- `authoredPresets` bindings for selected and recursively included presets.
- `appliedStepTemplates` entries with compact composition metadata.
- Existing non-preset submission fields preserved.

## Failure Behavior

Submission finalization is blocked before execution work is created when preset compilation detects:
- Cycles.
- Missing preset references.
- Disabled or unauthorized presets.
- Version mismatches.
- Conflicting aliases.
- Incompatible input mappings.
- Unsupported unresolved preset include work in worker-facing steps.

## Invariants

- Preset composition is compile-time control-plane behavior.
- Submitted execution payloads do not require live preset lookup.
- Task snapshots preserve pinned bindings, include-tree summary, per-step provenance, detachment state, and final submitted order.
- Manual-only submissions do not fabricate preset provenance.
- Compact provenance is stored instead of full template content.
