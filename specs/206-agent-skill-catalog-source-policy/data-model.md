# Data Model: Agent Skill Catalog and Source Policy

## AgentSkillDefinition

Represents a reusable agent instruction bundle definition.

Fields:
- `id`: stable definition identity.
- `slug`: unique human and API identifier.
- `title`: operator-facing display title.
- `description`: optional explanatory text.
- `author`: optional author/provenance text.
- `created_at`, `updated_at`: audit timestamps.
- `versions`: ordered AgentSkillVersion records.

Validation rules:
- `slug` is unique.
- A definition can have zero or more immutable versions.
- Definition metadata may change, but version content is preserved by version rows.

## AgentSkillVersion

Represents one immutable content release for a deployment-stored agent skill.

Fields:
- `id`: stable version row identity.
- `skill_id`: owning AgentSkillDefinition.
- `version_string`: version label unique per skill.
- `format`: markdown or bundle.
- `artifact_ref`: artifact-backed content reference.
- `content_digest`: content integrity digest.
- `created_at`: immutable creation timestamp.

Validation rules:
- `(skill_id, version_string)` is unique.
- Creating a later version does not mutate earlier versions.
- Large content is stored by artifact reference rather than inline workflow payloads.

## SkillSet

Represents a named collection of agent-skill selections or rules.

Fields:
- `id`, `slug`, `title`, `description`.
- `entries`: SkillSetEntry records.
- `created_at`, `updated_at`.

Validation rules:
- `slug` is unique.
- Skill sets reference AgentSkillDefinition records rather than executable ToolDefinition records.

## SkillSetEntry

Represents one selected skill inside a SkillSet.

Fields:
- `id`: stable entry identity.
- `skill_set_id`: owning SkillSet.
- `skill_id`: referenced AgentSkillDefinition.
- `version_constraint`: optional version selector.
- `created_at`: entry creation timestamp.

Validation rules:
- A SkillSet cannot include the same AgentSkillDefinition twice.
- Version constraints select agent-skill versions, not executable tool contracts.

## SkillResolutionContext

Represents policy and runtime context used during source resolution.

Fields:
- `snapshot_id`: target ResolvedSkillSet identity.
- `deployment_id`: optional deployment scope.
- `workspace_root`: optional checked-out workspace root.
- `allow_repo_skills`: whether repo-checked-in skills may participate in resolution.
- `allow_local_skills`: whether local-only skills may participate in resolution.
- `async_session_maker`: optional deployment catalog session factory.

Validation rules:
- Repo skill candidates are excluded when `allow_repo_skills` is false.
- Local-only skill candidates are excluded when `allow_local_skills` is false.
- Denied sources are excluded before precedence and materialization.

## ResolvedSkillSet

Represents the immutable resolved active skill set for a run or step.

Fields:
- `snapshot_id`: immutable snapshot identity.
- `deployment_id`: optional deployment scope.
- `resolved_at`: resolution timestamp.
- `skills`: ordered ResolvedSkillEntry list.
- `manifest_ref`: optional artifact reference.
- `source_trace`: optional source diagnostics.
- `resolution_inputs`: compact selector and policy input summary.
- `policy_summary`: compact policy decision summary.

Validation rules:
- Entries are selected only from allowed source kinds.
- Each entry records source provenance.
- Runtime materialization consumes this snapshot rather than mutable source folders.

## ResolvedSkillEntry

Represents one selected skill in a resolved snapshot.

Fields:
- `skill_name`: selected skill identifier.
- `version`: selected version or source-local version marker.
- `format`: markdown or bundle.
- `content_ref`: optional artifact/content reference.
- `content_digest`: optional digest.
- `provenance`: AgentSkillProvenance.

Validation rules:
- `provenance.source_kind` is required.
- Denied source kinds cannot appear in final resolved entries.

## AgentSkillProvenance

Represents where a selected skill came from.

Fields:
- `source_kind`: built-in, deployment, repo, or local.
- `original_version`: optional original source version.
- `source_path`: optional repo/local source path.
- `skill_set_name`: optional selection source.

Validation rules:
- Source kind must reflect the source that won resolution.
- Repo/local path provenance is diagnostic only and must not make raw content trusted.
