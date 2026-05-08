# Task Preset Compilation Contract

## Boundary

Preset compilation is a control-plane boundary that runs before task execution finalization. The output is the authoritative submitted task contract.

## Input

A task draft may contain:
- manual steps,
- unresolved preset authoring steps,
- applied template metadata,
- task-level runtime, publish, repository, Jira, dependency, and attachment fields.

Each preset include must identify:
- preset slug or ID,
- pinned version,
- scope,
- optional alias,
- optional input mapping.

## Output

The submitted task contract contains:
- final ordered executable steps,
- no unresolved preset include work for workers,
- per-step source provenance for preset-derived, included, manual, or detached steps when reliable,
- authored preset bindings for root and included presets when reliable,
- recursive composition/include-tree summary suitable for audit and reconstruction,
- existing task-level runtime, publish, Jira, dependency, and attachment fields unchanged except for normalized canonical shape.

## Required Behavior

1. The control plane validates the full recursive include tree before execution finalization.
2. Invalid include trees fail explicitly and do not create worker-facing unresolved preset work.
3. Manual and preset-derived steps are flattened into deterministic final submitted order.
4. Submitted snapshots preserve enough compact metadata to reconstruct the authored preset composition after live catalog definitions change.
5. Workers consume only resolved executable steps and do not consult the live preset catalog to recover task structure.
6. Manual-only tasks remain valid and do not gain fabricated preset provenance.

## Compatibility And Failure Rules

- Do not add compatibility aliases for new internal preset metadata.
- Unsupported internal payload shapes fail explicitly.
- Existing attachment target, runtime, publish, Jira provenance, edit, rerun, and resume semantics remain unchanged unless they need compiled preset provenance for reconstruction.
- Large template or skill content must not be embedded in workflow history.
