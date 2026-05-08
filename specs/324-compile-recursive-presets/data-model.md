# Data Model: Compile Recursive Task Presets

## Task Draft

Represents the authoring state before submission.

Fields:
- `instructions`: objective text.
- `steps`: manual, skill, tool, or unresolved preset authoring steps.
- `appliedStepTemplates`: client-side record of templates expanded into the draft when present.
- `authoredPresets`: authored preset selections and bindings when present or derivable from expansion.
- `runtime`, `publish`, `git`, `dependsOn`, `inputAttachments`: existing task-level submission fields.

Validation rules:
- Unresolved preset authoring steps must be compiled before execution finalization.
- Manual-only drafts remain valid without preset metadata.
- Invalid preset references, cycles, disabled templates, unsupported include shapes, and incompatible mappings fail explicitly.

## Preset Include Tree

Represents the recursive template composition selected by the author.

Fields:
- `slug` or `presetSlug`: preset identifier.
- `version` or `presetVersion`: pinned version.
- `scope`: global or personal scope.
- `alias`: include alias when provided.
- `path` or `includePath`: ordered path from root preset to included preset or step.
- `inputMapping`: mapping used to supply child preset inputs.
- `includes`: child include nodes.
- `stepIds`: flattened executable step identifiers produced by this node.
- `requiredCapabilities`: compact capabilities contributed by the node.

Validation rules:
- Every include must resolve to an allowed active preset version before execution.
- Include paths must be deterministic and cycle-free.
- Include aliases must not create ambiguous bindings within the same include scope.

## Compiled Task Snapshot

Represents the authoritative submitted task after preset compilation.

Fields:
- `steps`: final ordered executable steps.
- `authoredPresets`: compact authored preset binding metadata for root and included presets.
- `appliedStepTemplates`: applied root template metadata plus recursive composition summary when available.
- `source`: per-step provenance for manual, preset-derived, preset-include, or detached steps.
- Existing task fields: runtime, publish, git, Jira provenance, dependencies, and attachments.

Validation rules:
- Snapshot reconstruction must not require live preset catalog lookup.
- Snapshot metadata must be compact and suitable for execution records/artifacts.
- Existing attachment target and runtime/publish semantics must not change.

## Worker-Facing Payload

Represents the task contract consumed by execution workers.

Fields:
- `steps`: executable skill/tool/manual steps only.
- `source`: optional compact provenance.
- `authoredPresets` and `appliedStepTemplates`: optional audit/reconstruction metadata.

Validation rules:
- No unresolved preset include objects are accepted as worker work.
- Workers consume the resolved steps they receive and do not expand presets.

## State Transitions

- `draft-authored` -> `preset-compiled`: recursive include tree resolves and flattened executable steps are produced.
- `draft-authored` -> `rejected`: include tree is invalid, disabled, unauthorized, cyclic, or incompatible.
- `preset-compiled` -> `submitted-snapshot`: flattened steps and provenance are persisted as authoritative submitted task state.
- `submitted-snapshot` -> `worker-received`: worker receives resolved executable steps without live catalog lookup.
- `submitted-snapshot` -> `reconstructed`: edit/rerun/audit reconstructs from snapshot even if live preset definitions changed.
