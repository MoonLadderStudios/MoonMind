# Contract: Skill Projection Runtime Boundary

## Materialization Metadata

Managed runtime materialization returns compact metadata with these required fields:

```json
{
  "activeSkills": ["pr-resolver"],
  "backingPath": "/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>",
  "visiblePath": "/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>",
  "canonicalAliasPath": ".agents/skills",
  "canonicalAliasAvailable": false,
  "canonicalAliasSkippedReason": "repo_authored_skills_present",
  "repoSkillSourcePreserved": true,
  "manifestPath": "/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>/_manifest.json",
  "compatibilityPaths": {
    "agentsSkillsAvailable": false,
    "agentsSkillsPath": "/work/agent_jobs/<job_id>/repo/.agents/skills",
    "agentsSkillsStatus": "skipped",
    "geminiSkillsAvailable": false,
    "geminiSkillsPath": "/work/agent_jobs/<job_id>/repo/.gemini/skills",
    "geminiSkillsStatus": "skipped"
  }
}
```

Rules:
- `visiblePath` is authoritative for the agent activation summary.
- `.agents/skills` is only a compatibility alias when `canonicalAliasAvailable` is true.
- `repoSkillSourcePreserved` must be true when `.agents/skills` is a repo-authored directory.
- The payload must not include full skill bodies.

## Activation Summary

When a selected skill is active, managed runtime instructions must include:

```text
Active MoonMind skill snapshot:
- Selected skill: <skill-name>
- Full active MoonMind skill content is available at: <visiblePath>
- Read `<visiblePath>/<skill-name>/SKILL.md` first and follow that active snapshot.
- Do not discover skills from repo-local or local-only source folders during execution.
```

When `.agents/skills` is repo-authored and alias unavailable, the summary must also state that the repository `.agents/skills` directory is source content and must not be treated as the active selected skill snapshot.

## Alias Decision Outcomes

| Existing path | Required outcome |
| --- | --- |
| `.agents/skills` missing | create alias when adapter requires and path is safe |
| `.agents/skills` symlink to active backing path | reuse alias |
| `.agents/skills` MoonMind-owned stale symlink | replace alias after ownership proof |
| `.agents/skills` repo-authored directory | skip alias and use run-scoped `visiblePath` |
| `.agents/skills` file | fail before launch |
| `.agents/skills` unknown symlink | fail before launch unless ownership is proven |

## Loader Guard Contract

- Built-in skill loading must not read current working directory `.agents/skills` as a built-in source.
- Repo skill loading must fail or skip with explicit contamination diagnostics when `.agents/skills` is a MoonMind active projection.
- Local skill loading must fail or skip with explicit contamination diagnostics when `.agents/skills/local` is hidden by a MoonMind active projection.

## Publish And Verification Contract

- Publish filtering excludes `.agents/skills`, `.gemini/skills`, and `skills_active` only when the path is MoonMind-owned projection state.
- Real repo-authored `.agents/skills` content remains publishable.
- MoonSpec verification preflight checks `.agents/skills`, `.gemini/skills`, and `skills_active` before full-suite evidence is classified.
- If contamination cannot be repaired, verification returns or records `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION` rather than an indeterminate feature verdict.
