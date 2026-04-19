# Data Model: Skill Runtime Observability and Verification

## SkillRuntimeEvidence

Represents compact skill runtime metadata exposed on execution detail surfaces.

Fields:
- `resolvedSkillsetRef`: optional string identifying the immutable resolved skill snapshot.
- `selectedSkills`: list of selected skill names from task selectors or materialization metadata.
- `selectedVersions`: optional list of selected skill/version/source tuples when available.
- `sourceProvenance`: optional list or summary of source kinds and source paths when available.
- `materializationMode`: optional string describing how the runtime received the skill set.
- `visiblePath`: optional canonical runtime-visible path summary.
- `backingPath`: optional run-scoped active backing path summary.
- `readOnly`: optional boolean indicating whether the projection or backing store was read-only where known.
- `manifestRef`: optional artifact ref or path to the active manifest.
- `promptIndexRef`: optional artifact ref or compact prompt-index ref.
- `activationSummaryRef`: optional ref or safe summary label for the activation summary evidence.
- `diagnostics`: optional `ProjectionDiagnostic` when materialization failed.

Validation:
- Must contain only metadata, paths, booleans, names, versions, source summaries, and refs.
- Must not contain full `SKILL.md` bodies or large manifest content.
- Empty or unknown values should be omitted or rendered as unavailable rather than guessed.

## SkillVersionSummary

Represents one selected skill entry in a compact operator-safe form.

Fields:
- `name`: skill name.
- `version`: optional selected version.
- `sourceKind`: optional source kind such as built-in, deployment, repo, or local.
- `sourcePath`: optional source path summary when safe and available.
- `contentRef`: optional artifact/content ref.
- `contentDigest`: optional digest.

Validation:
- Full content is never included.
- Source paths are metadata only and must not reveal credentials.

## ProjectionDiagnostic

Represents an operator-visible projection failure.

Fields:
- `path`: path involved in the failure.
- `objectKind`: object kind such as file, directory, symlink, special, or missing.
- `attemptedAction`: attempted projection/materialization action.
- `remediation`: operator-safe remediation guidance.
- `cause`: optional sanitized lower-level cause.

Validation:
- Must not include full skill bodies, raw credentials, or environment dumps.
- Must be safe for standard operator-visible diagnostics.

## SkillLifecycleIntent

Represents how skill intent survives proposal, schedule, rerun, retry, and replay paths.

Fields:
- `source`: proposal, schedule, rerun, retry, continue-as-new, or replay.
- `selectors`: optional selector summary for runs that should resolve at launch.
- `resolvedSkillsetRef`: optional snapshot ref for runs that must reuse a previous snapshot.
- `resolutionMode`: selector-based, snapshot-reuse, inherited-defaults, or explicit-re-resolution.
- `explanation`: short operator-facing explanation of how the skill snapshot was or will be selected.

Validation:
- Rerun, retry, continue-as-new, and replay default to snapshot reuse when a resolved snapshot exists.
- Inheriting deployment defaults must be explicit, not silent.
- Explicit re-resolution must be distinguishable from snapshot reuse.

## State Transitions

- Submit-time selector -> unresolved skill intent.
- Runtime resolution -> immutable `resolvedSkillsetRef` and selected skill summaries.
- Runtime materialization -> `SkillRuntimeEvidence` with visible/backing paths, refs, and diagnostics.
- Proposal/schedule -> preserved selector intent or explicit default-inheritance note.
- Rerun/retry/replay -> original `resolvedSkillsetRef` reuse unless explicit re-resolution is requested.
