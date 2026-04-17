# Contract: Flattened Plan Execution

## Jira Traceability

This contract implements the MM-386 runtime architecture story. MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata must preserve `MM-386`.

## Preset Expansion Boundary

The canonical plan contract must state:

- preset composition is an authoring concern,
- preset includes are resolved before a plan is stored for execution,
- the stored plan artifact is the flattened execution contract after expansion,
- runtime behavior does not depend on nested preset semantics.

## Stored Plan Artifact Contract

A stored plan artifact must contain only:

- executable plan nodes,
- dependency edges,
- policies,
- artifact references,
- executable tool contracts and inputs,
- optional non-executable source provenance metadata.

Unresolved include objects are invalid stored plan artifact content.

## Plan Node Provenance Contract

Plan nodes may include optional `source` metadata with:

- `binding_id`,
- `include_path`,
- `blueprint_step_slug`,
- `detached`.

The metadata is traceability-only. It must never select tools, alter node inputs, change dependency behavior, override policies, or otherwise become executable runtime logic.

## Validation Contract

Plan validation must:

- allow absent provenance on otherwise valid executable nodes,
- accept structurally valid provenance as metadata,
- reject unresolved preset include entries before execution,
- reject structurally invalid claimed preset provenance,
- keep validation failures explicit instead of silently dropping malformed authoring output.

## DAG Semantics Contract

Manual authoring, preset expansion, and other plan-producing tools all produce the same flattened node-and-edge graph for execution.

The executor consumes:

- node readiness from dependency edges,
- failure behavior from policy,
- tool invocation details from node tool contracts and inputs,
- artifact references from node inputs or prior outputs.

The executor does not consume:

- live preset includes,
- nested preset trees,
- provenance as executable logic,
- authoring-time fallback semantics.

## Validation Evidence

The story is complete when `docs/Tasks/SkillAndPlanContracts.md` contains the contract language above and final MoonSpec verification confirms coverage of FR-001 through FR-012 and DESIGN-REQ-001, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-025, and DESIGN-REQ-026.
