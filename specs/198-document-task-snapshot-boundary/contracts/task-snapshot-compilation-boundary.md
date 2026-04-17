# Contract: Task Snapshot And Compilation Boundary

## Jira Traceability

This contract implements the MM-385 runtime architecture story. MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata must preserve `MM-385`.

## Control-Plane Compilation Contract

Before a submitted task reaches execution, the control plane must:

- resolve recursively composable preset authoring objects,
- validate the include tree and rejected states,
- flatten preset-derived and manual steps into final submitted order,
- preserve provenance for preset-derived and detached steps,
- finalize a worker-facing resolved execution payload.

The execution plane must not receive unresolved preset composition that requires live catalog lookup.

## Task Payload Metadata Contract

The representative task payload must allow:

- optional `authoredPresets` metadata at the task level,
- optional `source` metadata at the step level,
- resolved concrete step instructions and structured inputs for execution,
- preservation of manual and preset-derived ordering.

The metadata is optional for simple manual tasks, but when preset composition contributed to the submitted task, the snapshot must preserve enough metadata for reconstruction and audit.

## Snapshot Durability Contract

The authoritative task input snapshot must preserve:

- pinned preset bindings,
- include-tree summary,
- per-step provenance,
- detachment state,
- final submitted order.

The snapshot remains the source for edit, rerun, audit, and diagnostics after preset catalog changes. Execution projections and historical logs may support diagnostics, but they are not an authoritative replacement.

## Worker Boundary Contract

Runtime workers consume resolved payloads. They do not:

- expand presets,
- read live preset catalog state to recover missing task structure,
- reinterpret authored preset bindings,
- depend on current preset catalog correctness for already submitted work.

## Validation Evidence

The story is complete when `docs/Tasks/TaskArchitecture.md` contains the contract language above and final MoonSpec verification confirms coverage of FR-001 through FR-009 and DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-025, and DESIGN-REQ-026.
