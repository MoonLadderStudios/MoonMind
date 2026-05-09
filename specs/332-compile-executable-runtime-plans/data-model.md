# Data Model: Compile Executable Steps into Runtime Plans

## Executable Step

Durable task step that can be compiled into runtime work without additional authoring expansion.

Fields:

- `id`: stable step identifier used for plan node identity and step ledger rows.
- `type`: canonical executable Step Type; valid runtime values are `tool` and `skill`.
- `title`: optional operator-facing label.
- `instructions`: step-scoped instructions or fallback task instructions.
- `tool`: Tool step binding with id/name, optional version, and inputs.
- `skill`: Skill step binding with id/name, optional version, and inputs.
- `source`: optional preset/source provenance metadata.

Validation:

- `type` must be `tool` or `skill` for executable payloads.
- `tool` steps require a valid tool payload and must not carry conflicting skill payloads.
- `skill` steps require a valid skill payload or accepted skill selector shape.
- `preset` and unresolved include work are rejected before runtime plan execution.

## Runtime Plan Node

Runtime representation derived from one executable step.

Fields:

- `id`: plan node id, normally from the step id.
- `tool.type`: runtime executor discriminator such as `skill` or `agent_runtime`.
- `tool.name`: selected tool, skill, activity, or runtime mode name.
- `tool.version`: selected version when applicable.
- `inputs`: merged task, step, tool, skill, source, and instruction inputs needed for execution.
- `edges`: sequential dependencies between generated nodes when multiple steps are present.

Validation:

- Tool nodes must carry enough identity and input data to resolve a typed execution path.
- Skill nodes may materialize as skill activities, child workflows, plan nodes, or managed-session requests.
- Unsupported `tool.type` values fail explicitly.

## Preset Provenance

Audit and reconstruction metadata attached to preset-derived executable work.

Fields:

- `kind`: `manual`, `preset-derived`, `preset-include`, or `detached`.
- `presetId` or `presetSlug`: source preset identity.
- `presetVersion`: source preset version.
- `includePath`: ordered preset include path when the step came from recursive composition.
- `originalStepId`: step id from the source preset when available.

Validation:

- Provenance is optional for manual work.
- Provenance does not authorize runtime catalog lookup after expansion.
- Provenance must stay compact and safe for workflow payloads and proposal metadata.

## Promoted Proposal Payload

Reviewed task payload promoted from a proposal into execution.

Fields:

- `task.steps`: flattened executable Tool/Skill steps.
- `task.authoredPresets`: optional authored preset bindings.
- `task.appliedStepTemplates`: optional applied template summary and composition metadata.
- `task.sourceSteps`: optional compact source-step metadata for proposal candidates.

Validation:

- Promotion validates the stored reviewed payload.
- Promotion rejects unresolved Preset steps and preset-derived steps that are not flattened executable steps.
- Promotion must not silently re-expand a live preset catalog entry.

## State Transitions

```text
Draft Preset Step
  -> preview/apply or submit-time expansion
  -> Executable Step list with provenance
  -> Durable task payload / reviewed proposal payload
  -> Runtime Plan Nodes
  -> Temporal activity, child workflow, managed session, or typed tool execution
```

Invalid path:

```text
Unresolved Preset Step
  -> runtime plan node
```

This path is rejected unless a future explicit linked-preset runtime mode is specified in a separate story.
