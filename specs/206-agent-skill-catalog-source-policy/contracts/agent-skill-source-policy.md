# Contract: Agent Skill Source Policy

## Purpose

This contract defines the observable resolver behavior for MM-405. It is intentionally runtime-facing: callers provide compact selection and source policy inputs, and the resolver returns an immutable ResolvedSkillSet snapshot containing only policy-allowed agent instruction bundles.

## Inputs

### SkillSelector

- `sets`: optional skill-set slugs.
- `include`: optional explicit skill names and version pins.
- `exclude`: optional skill names to exclude.
- `materialization_mode`: optional preferred runtime materialization mode.

### SkillResolutionContext

- `snapshot_id`: required snapshot identifier for the resolved output.
- `deployment_id`: optional deployment identifier.
- `workspace_root`: optional workspace root for repo/local candidate discovery.
- `allow_repo_skills`: required effective policy decision for repo-checked-in skills.
- `allow_local_skills`: required effective policy decision for local-only skills.
- `async_session_maker`: optional deployment catalog session factory.

## Source Policy Rules

1. Built-in and deployment-stored sources may be considered when their loaders are configured.
2. Repo-checked-in source candidates MUST NOT be loaded or returned when `allow_repo_skills` is false.
3. Local-only source candidates MUST NOT be loaded or returned when `allow_local_skills` is false.
4. Denied source candidates MUST be excluded before precedence, selector filtering, and materialization.
5. A denied repo or local candidate MUST NOT appear in `ResolvedSkillSet.skills`, `source_trace`, or runtime materialization output as an active skill.
6. Allowed source precedence remains built-in < deployment < repo < local.
7. The final snapshot MUST record compact policy summary data sufficient to explain whether repo and local sources were allowed.

## Outputs

### ResolvedSkillSet

The resolver returns:

- `snapshot_id` matching the input context.
- `resolved_at` set to resolution time.
- `skills` containing only policy-allowed selections.
- `resolution_inputs` containing compact selector data.
- `policy_summary` containing `repo_skills_allowed`, `local_skills_allowed`, and loaded source information.

## Error Behavior

- Duplicate skill definitions within the same source fail resolution.
- Pinned version mismatches fail resolution.
- Unknown loader/source kinds fail resolution.
- Unsupported or malformed policy state fails closed by excluding untrusted repo/local sources.

## Non-Goals

- This contract does not define executable ToolDefinition behavior.
- This contract does not move raw skill body content into workflow history.
- This contract does not mutate checked-in `.agents/skills` folders during runtime materialization.
