# Data Model: Skill Projection Noninterference

## Active Skill Snapshot

Represents the immutable selected skill set for one run or step.

Fields:
- `snapshotId`: stable resolved snapshot identifier.
- `activeSkills`: ordered selected skill names.
- `resolvedSkillsetRef`: artifact reference for the compact resolved skill set.
- `manifestPath`: path or artifact reference for runtime-visible manifest evidence.

Validation rules:
- Snapshot ID in `_manifest.json` must match the resolved skill set used for the turn.
- Active skill names in metadata must match the materialized selected skill directories.
- Skill bodies remain on disk or in artifacts; workflow/activity payloads carry compact refs and metadata only.

## Runtime Skill Materialization

Represents how an active snapshot is exposed to a managed runtime.

Fields:
- `backingPath`: MoonMind-owned run-scoped active skill directory.
- `visiblePath`: path the agent must read for the active selected skill snapshot.
- `canonicalAliasPath`: compatibility alias path, normally `.agents/skills`.
- `canonicalAliasAvailable`: whether the compatibility alias points at the active snapshot.
- `canonicalAliasSkippedReason`: null when alias is available; otherwise a stable reason such as `repo_authored_skills_present`.
- `repoSkillSourcePreserved`: whether existing repo skill sources remain readable and unmodified.
- `compatibilityPaths`: optional adapter alias status for `.agents/skills` and `.gemini/skills`.

Validation rules:
- `visiblePath` must exist, contain `_manifest.json`, and contain the selected skill `SKILL.md` before runtime launch.
- If `.agents/skills` is repo-authored, `canonicalAliasAvailable` must be false and `visiblePath` must point to the run-scoped active path.
- If an alias is replaced, ownership must be proven before unlinking.
- Unknown symlinks and files fail before launch with actionable diagnostics.

State transitions:
1. `planned`: runtime has selected a resolved skill set and target workspace.
2. `materialized`: active backing store exists and manifest is written.
3. `alias_created`, `alias_reused`, `alias_skipped`, or `alias_blocked`: compatibility alias decision is recorded.
4. `validated`: visible path manifest and selected skill are confirmed before launch.
5. `failed`: launch is blocked before execution due to unsafe projection or invalid active path.

## Projection Alias

Optional compatibility path used by adapters.

Fields:
- `path`: `.agents/skills` or `.gemini/skills`.
- `status`: `created`, `reused`, `skipped`, `blocked`, or `failed`.
- `available`: whether runtime may use this alias.
- `reason`: structured explanation when unavailable.

Validation rules:
- Missing path may be created.
- Existing symlink may be reused when it already targets the active backing store.
- Existing symlink may be replaced only if it resolves under MoonMind-owned active roots or contains MoonMind manifest evidence.
- Existing directory, file, or unknown symlink in a publishable checkout must not be replaced.

## Repo Skill Source

Repo-authored skill inputs under `.agents/skills` or local overlay `.agents/skills/local`.

Fields:
- `path`: source directory path.
- `sourceKind`: `repo` or `local`.
- `preserved`: whether source remains visible and unchanged during runtime materialization.

Validation rules:
- Repo and local loaders must not scan a MoonMind active projection as source input.
- Local overlay hidden by an active projection must produce explicit contamination diagnostics.
- Publish filtering must preserve real repo-authored source directories.

## Projection Diagnostic

Operator-facing evidence for projection decisions.

Fields:
- `event`: stable event name, such as `skill_projection_alias_skipped` or `skill_projection_alias_blocked`.
- `workspace`: affected workspace.
- `aliasPath`: compatibility alias path.
- `status`: alias decision.
- `reason`: stable skip/block/failure reason.
- `activeVisiblePath`: selected active path.
- `snapshotId`: resolved skill snapshot ID.

Validation rules:
- Diagnostics must not include raw skill bodies or credentials.
- Failure diagnostics must include conflicting path, object kind, attempted action, and remediation guidance.
