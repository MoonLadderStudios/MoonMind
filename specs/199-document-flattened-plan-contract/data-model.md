# Data Model: Document Flattened Plan Execution Contract

This story documents plan artifact shape, provenance metadata, and validation semantics. It introduces no new persistent storage.

## Stored Plan Artifact

Represents the durable execution contract accepted by the runtime executor after authoring and expansion are complete.

Fields:
- `plan_version`: Supported plan contract version.
- `metadata`: Plan title, creation metadata, and pinned registry snapshot reference.
- `policy`: Failure mode and concurrency policy.
- `nodes`: Executable plan nodes only.
- `edges`: Dependency graph between executable nodes.

Validation rules:
- Stored plans are flat DAG execution contracts.
- Stored plans do not contain unresolved preset include entries.
- Runtime behavior depends on nodes, edges, policies, artifacts, and tool contracts.

## Executable Plan Node

Represents one runtime-executable unit in a stored plan.

Fields:
- `id`: Stable node identifier.
- `title`: Display-safe node title.
- `tool`: Executable tool selection.
- `inputs`: Tool inputs or deterministic references to prior outputs.
- `options`: Optional execution policy overrides.
- `source`: Optional source provenance metadata.

Validation rules:
- Node IDs are unique within a plan.
- Node tool references resolve through the pinned tool registry snapshot.
- Node inputs validate against the selected tool input schema.
- `source` may be absent when no provenance is available or needed.

## Unresolved Include Entry

Represents a preset composition object that still requires authoring-time expansion.

Fields:
- `include`: Preset or blueprint include reference.
- `inputs`: Include-time input overrides.
- `path`: Authoring include path when nested.

Validation rules:
- Unresolved include entries are invalid in stored plan artifacts.
- Include resolution must complete before the execution-facing plan artifact is stored.
- Validators reject unresolved include entries before execution begins.

## Source Provenance Metadata

Represents optional traceability for how an executable node was produced.

Fields:
- `binding_id`: Authored preset binding or composition binding that produced the node.
- `include_path`: Ordered include path from the authoring composition tree.
- `blueprint_step_slug`: Source blueprint or preset step slug.
- `detached`: Whether the node has been detached from live preset identity.

Validation rules:
- Provenance is optional.
- Valid provenance is accepted as traceability metadata.
- Structurally invalid claimed preset provenance is rejected.
- Provenance never changes executable logic.

## Flattened Execution Graph

Represents the authoring-origin-neutral DAG consumed by the executor.

Fields:
- `nodes`: Executable plan nodes.
- `edges`: Dependencies between nodes.
- `policy`: Execution policy for ready-node scheduling and failure handling.
- `artifactRefs`: Artifact references used by node inputs or outputs.

Validation rules:
- Manual authoring, preset expansion, and other plan-producing tools all produce the same graph shape.
- Nested preset semantics do not exist at runtime.
- The executor follows dependency and policy semantics only.

## State Transitions

1. Plan producer authors manual nodes, preset composition, or other plan-producing output.
2. Authoring-time composition resolves preset includes and produces executable nodes.
3. Plan producer stores a flat plan artifact with optional source provenance.
4. Validation rejects unresolved includes or structurally invalid claimed provenance.
5. Runtime executor consumes the flat graph and ignores provenance for execution decisions.
6. Audit or diagnostics use provenance metadata for traceability when present.
