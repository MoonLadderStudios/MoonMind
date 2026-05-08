# Data Model: Disabled Skills On Demand Controls

No new persistent database tables or durable storage models are planned. The story defines runtime contracts, settings, and transient command results.

## Entity: SkillsOnDemandControl

Purpose: Operator-facing global feature gate for managed-runtime Skills On Demand.

Fields:
- `enabled`: boolean, default `false`.
- `source`: optional diagnostic string identifying which configuration source supplied the effective value.
- `aliases`: the accepted operator-facing names `MOONMIND_SKILLS_ON_DEMAND_ENABLED` and `WORKFLOW_SKILLS_ON_DEMAND_ENABLED`.

Validation rules:
- Missing value resolves to `enabled=false`.
- Blank or unrecognized values must not become permissive.
- When multiple sources are present, existing configuration precedence resolves one deterministic effective value.

## Entity: SkillsOnDemandQueryResult

Purpose: Runtime result for an on-demand Skill metadata query.

Fields when disabled:
- `status`: `denied`.
- `code`: `feature_disabled`.
- `message`: human-readable disabled explanation.
- `results`: empty list.

Validation rules:
- Disabled query results must not include full Skill bodies, content refs, or catalog entries.
- Disabled query handling must complete before catalog lookup.

## Entity: SkillsOnDemandRequestResult

Purpose: Runtime result for an on-demand request to add Skills.

Fields when disabled:
- `status`: `denied`.
- `code`: `feature_disabled`.
- `message`: human-readable disabled explanation.
- `resolved_skillset_ref`: absent.
- `snapshot_id`: absent.
- `activation_summary`: absent.

Validation rules:
- Disabled request handling must not invoke Skill resolution, artifact persistence, materialization, or runtime activation update.
- Disabled request handling must not create a derived `ResolvedSkillSet`.

## Entity: ManagedRuntimeActivationCapability

Purpose: Compact metadata or text used when preparing managed runtimes.

Fields:
- `skills_on_demand_enabled`: boolean effective value.
- `commands_exposed`: boolean indicating whether on-demand commands are available to the runtime.
- `disabled_message`: optional text used when command exposure cannot be fully hidden.

Validation rules:
- When disabled and command exposure is controllable, `commands_exposed=false`.
- When disabled and command exposure cannot be fully hidden, `disabled_message` must tell the agent that Skills On Demand is disabled for the run.
- Existing active Skill snapshot refs and visible paths remain unchanged.

## State Transitions

```text
unset setting -> disabled
explicit false through either alias -> disabled
disabled + query attempt -> denied(feature_disabled), no results
disabled + request attempt -> denied(feature_disabled), no derived snapshot
disabled + initial Skill launch -> initial active snapshot remains available
```

