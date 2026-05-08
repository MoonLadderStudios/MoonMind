# Contract: Disabled Skills On Demand Runtime Boundary

## Configuration Contract

The deployment exposes one effective boolean control:

```json
{
  "skillsOnDemandEnabled": false
}
```

Accepted operator-facing setting names:

```text
MOONMIND_SKILLS_ON_DEMAND_ENABLED
WORKFLOW_SKILLS_ON_DEMAND_ENABLED
```

Rules:
- Default is `false`.
- Both names map to the same effective control.
- Existing configuration precedence resolves conflicts.
- The control gates whether on-demand query/request paths are callable at all.
- Existing Skill source, version, runtime compatibility, and policy checks remain authoritative for normal Skill resolution.

## Query Contract

Command name:

```text
moonmind.skills.query
```

Disabled response:

```json
{
  "status": "denied",
  "code": "feature_disabled",
  "message": "Skills On Demand is disabled for this deployment.",
  "results": []
}
```

Rules:
- The disabled response is returned before catalog lookup.
- `results` is always an empty list while disabled.
- The response must not include full Skill bodies or content refs.

## Request Contract

Command name:

```text
moonmind.skills.request
```

Disabled response:

```json
{
  "status": "denied",
  "code": "feature_disabled",
  "message": "Skills On Demand is disabled for this deployment."
}
```

Rules:
- The disabled response is returned before Skill resolution.
- No derived `ResolvedSkillSet` is created.
- No new active snapshot is materialized.
- No activation update is emitted.

## Managed Runtime Activation Contract

When Skills On Demand is disabled and commands can be hidden:

```json
{
  "skillsOnDemandEnabled": false,
  "commands": []
}
```

When commands cannot be hidden, activation text includes:

```text
Skills On Demand is disabled for this run. Use only the active Skills already available under the active skill path provided by MoonMind.
```

Rules:
- The initial active Skill snapshot summary remains present when a selected Skill exists.
- On-demand runtime commands are not Agent Skills and must not appear inside a `ResolvedSkillSet`.
- The runtime activation contract must keep large Skill bodies out of workflow history.

## Side-Effect Contract

While disabled:
- query attempts create zero catalog results;
- request attempts create zero derived snapshots;
- resolver and materializer side effects are not invoked for on-demand attempts;
- normal initial selected Skill resolution remains unchanged.

