# Phase 0: Research for Provider Profiles Phase 3

## Existing System Context

The MoonMind agent runtime orchestration uses Temporal workflows to queue and dispatch worker environments. Currently, the system uses a `auth-profile-manager` workflow ID naming paradigm and `AuthProfileManager` string constants in `TemporalWorkflowType`. Furthermore, the `AgentExecutionRequest` expects `execution_profile_ref` to explicitly specify an authorization profile id, and falls back to string `"auto"`, but the Provider Profiles spec states that `ProviderProfileSelector` and a strictly optional strict-ref should manage dynamic workload distribution.

## Requirements from Spec
- Rename the manager implementation and its corresponding string IDs to `ProviderProfileManager`.
- Switch `execution_profile_ref` to an optional field indicating exact-referencing semantics, deprecating `"auto"`.
- Honor selector-based semantics (`provider_id`, `tags_any`, `tags_all`) out of `profile_selector`.
- Tie-break equal capabilities using highest priority then max max available slots.
- Snapshots of profiles must be mapped to `ManagedRuntimeProfile` cleanly honoring the `cooldown` timers.

## Proposed Strategy

1. **Rename the Class**:
   Locate `AuthProfileManager` across `moonmind/workflows/temporal/workflows/auth_profile_manager.py` (which likely needs moving to `provider_profile_manager.py`). Rename internal variables and workflow execution identifiers (via `workflow_id_reuse_policy` or fallback parsing) to prevent in-flight panics.
2. **Update Adapter Code / Callers**:
   Update `moonmind/workflows/adapters/managed_agent_adapter.py` and `moonmind/workflows/temporal/activities/auth_profile_manager_activities.py` to `provider_profile_manager_activities.py` etc. Update `moonmind/schemas/agent_runtime_models.py` imports/logic if necessary.
3. **Enhance Manager Selector Logic**:
   Inside `provider_profile_manager.py`, the dynamic slot leasing logic currently has tie-breaker evaluations. These must be updated to factor in explicit `provider_id` constraints, then `tags_any`/`all` and priority numbers stored in profiles.
4. **AgentRun snapshot injection**:
   Currently, AgentRun fetches profile configuration in `AgentRun._launch_runtime`. Let's ensure `cooldown` and `model_overrides` properties are seamlessly incorporated into AgentRun retry handlers via the updated profile payload (`ManagedRuntimeProfile`).

## Non-trivial Decisions

**In-flight Workflows Migration**: Changing a workflow's programmatic name breaks Temporal determinism for existing runs. To smoothly migrate, the existing `AuthProfileManager` might simply be deprecated and a new `ProviderProfileManager` scheduled natively, while gracefully draining the old one, but the simplest approach allowed by Principle XIII (Delete, Don't Deprecate) is to rename all occurrences and cancel/re-create the workflow for each runtime, since this workflow maintains ephemeral state (lease tracking that can be repopulated) or relies on persistent cooldowns stored elsewhere (DB or memory, easily restorable in MoonMind).
