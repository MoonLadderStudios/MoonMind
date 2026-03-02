# Runtime Contract: Skills Stage Execution

## Stage Input Contract

Every stage invocation must provide:

- `run_id` (UUID)
- `feature_id` (string)
- `stage` (`discover_next_phase|submit_codex_job|apply_and_publish`)
- `requested_skill_id` (optional string)
- `payload` (stage-specific object)
- `metadata` (optional object with workflow and queue context)

## Skill Resolution Contract

1. If `requested_skill_id` is present and policy permits it, resolve the mapped adapter.
2. Otherwise use configured stage/default skill settings.
3. If no adapter is registered for the resolved skill, fail fast with an adapter resolution error.
4. If adapter execution fails and fallback is enabled, execute direct fallback path.

## Output Contract

Stage execution returns:

- `run_id`
- `stage`
- `selected_skill_id`
- `adapter_id`
- `execution_path` (`skill|direct_fallback|direct_only`)
- `status` (`succeeded|failed`)
- `duration_ms`
- `artifacts` (list of artifact descriptors)
- `error` (optional)

## Metadata Normalization Contract

- Persisted metadata payload keys:
  - `selectedSkill`
  - `adapterId`
  - `executionPath`
  - `usedSkills`
  - `usedFallback`
  - `shadowModeRequested`
- API phase payload projection:
  - `selected_skill <- selectedSkill`
  - `adapter_id <- adapterId`
  - `execution_path <- executionPath`
- Legacy fallback defaults for Speckit phases:
  - missing `selectedSkill` -> `speckit`
  - missing `adapterId` with Speckit selection -> `speckit`
  - missing `executionPath` with Speckit selection -> `skill`

## Observability Contract

Each stage attempt emits structured fields:

- `run_id`
- `feature_id`
- `stage`
- `queue`
- `selected_skill_id`
- `adapter_id`
- `execution_path`
- `used_skills`
- `used_fallback`
- `shadow_mode_requested`
- `status`
- `duration_ms`

## Shared Skills Workspace Contract

When workspace materialization is active, stage payloads include:

- `skillsWorkspace.skillsActivePath`
- `skillsWorkspace.agentsSkillsPath`
- `skillsWorkspace.geminiSkillsPath`
- `skillsWorkspace.selectionSource`
- `skillsWorkspace.skills[]`
