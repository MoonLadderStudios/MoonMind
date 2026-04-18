# Data Model: Skill Selection and Snapshot Resolution

## Skill Selector

Represents task-wide or step-specific agent skill intent.

Fields:
- `sets`: Optional named skill sets to activate.
- `include`: Optional exact skill names and versions.
- `exclude`: Optional skill names to remove from inherited or resolved intent.
- `materializationMode`: Optional requested delivery mode for the runtime.

Validation:
- Selector arrays must be lists.
- Include entries require non-blank names and may include pinned versions.
- Invalid materialization modes fail validation before execution.

## Effective Skill Selector

Represents the deterministic merge of task-level and step-level selector intent.

Rules:
- Task-level `sets` and `include` provide inherited baseline intent.
- Step-level `sets` and `include` are additive.
- Step-level `exclude` removes matching inherited or step-level selections.
- Step-level `materializationMode`, when present, overrides the task-level mode for that step.
- Merging must not mutate the original task-level selector object.

## Source Policy

Represents which skill sources may contribute candidates during resolution.

Fields:
- `allow_repo_skills`: Whether repo-checked-in skills may be loaded.
- `allow_local_skills`: Whether `.agents/skills/local` skills may be loaded.
- Deployment and built-in source availability are determined by configured loader context.

Validation:
- Disallowed source candidates are excluded before precedence is applied.
- Unsupported or missing policy inputs fail closed for untrusted repo and local sources.

## ResolvedSkillSet

Immutable snapshot of selected skills for a run or step.

Fields:
- `snapshot_id`: Stable snapshot identity.
- `deployment_id`: Optional run or deployment context.
- `resolved_at`: Resolution timestamp.
- `skills`: Exact active skill entries with names, versions, provenance, and content refs.
- `manifest_ref`: Artifact-backed manifest reference when persisted.
- `source_trace`: Artifact-backed or compact provenance summary.
- `resolution_inputs`: Compact serialized selector inputs.
- `policy_summary`: Compact policy decisions used during resolution.

Validation:
- Resolved skills are sorted deterministically.
- Pinned versions must match exactly.
- Duplicate same-source names fail resolution.
- Missing required or pinned skills fail before runtime launch.

## Runtime Skill Materialization

Runtime-facing rendering of a `ResolvedSkillSet`.

Fields:
- `runtime_id`: Target runtime identity.
- `materialization_mode`: Prompt-bundled, workspace-mounted, hybrid, or retrieval mode.
- `workspace_paths`: Paths to materialized active skill snapshot data.
- `prompt_index_ref`: Optional prompt bundle or index ref.
- `retrieval_manifest_ref`: Optional retrieval manifest ref.
- `metadata`: Compact metadata for observability.

State transitions:
- `requested` -> `resolved` once `ResolvedSkillSet` is created.
- `resolved` -> `persisted` once manifest artifact refs are written.
- `persisted` -> `materialized` when runtime-facing content is prepared.
- Any resolution validation failure transitions to `failed_before_launch`.
