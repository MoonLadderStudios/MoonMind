# Contract: Runtime Skill Projection

## Boundary

The materialization boundary accepts an already resolved skill snapshot and prepares runtime-visible files for a managed runtime. It must not resolve skills, reload source directories, or mutate checked-in skill sources.

## Input

```text
ResolvedSkillSet
- snapshot_id
- resolved_at
- skills[]
  - skill_name
  - version
  - content_ref
  - content_digest
  - provenance.source_kind

runtime_id
materialization_mode
workspace_root
```

## Output

```text
RuntimeSkillMaterialization
- runtime_id
- materialization_mode
- workspace_paths includes .agents/skills
- prompt_index_ref when mode is hybrid or prompt_bundled
- metadata
  - visiblePath
  - backingPath
  - manifestPath
  - activeSkills[]
```

## Filesystem Contract

- `.agents/skills` is the canonical runtime-visible active skill path.
- `.agents/skills/_manifest.json` must exist for `workspace_mounted` and `hybrid` modes.
- `.agents/skills/<skill>/SKILL.md` must exist when the selected skill content is available.
- Unselected repo or local skills must not appear under `.agents/skills`.
- Compatibility links may exist, but they must target the same backing store as `.agents/skills`.

## Failure Contract

When projection cannot be established before runtime launch, the error must include:

- path
- object kind
- attempted action
- remediation guidance

Examples:

- `.agents` exists as a file.
- `.agents/skills` exists as a non-symlink directory.
- `.agents/skills` is a symlink that cannot be corrected or validated.

## Non-Goals

- Resolving skill selectors.
- Changing source precedence.
- Creating new persistent storage.
- Prompt-bundling full `SKILL.md` bodies for managed runtimes.
