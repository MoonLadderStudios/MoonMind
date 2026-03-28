# Tasks: Provider Profiles Phase 1 Migration

**Branch**: `109-provider-profiles-p1` | **Date**: 2026-03-28
**Spec**: `/Users/nsticco/MoonMind/specs/109-provider-profiles-p1/spec.md`
**Plan**: `/Users/nsticco/MoonMind/specs/109-provider-profiles-p1/plan.md`

## Phase 1: Setup

*(No setup tasks needed for this sprint)*

## Phase 2: Foundational

*(No foundational tasks needed)*

## Phase 3: User Story 1 (Renamed Subsystem Semantics)

Goal: Rename internal identifiers, workflows, and classes related to `AuthProfile` to `ProviderProfile`.
Independent Test: Types will compile without Error, unit tests pass without failure due to `ModuleNotFoundError`.

- [ ] T001 [US1] Rename file `moonmind/workflows/temporal/workflows/auth_profile_manager.py` to `provider_profile_manager.py`.
- [ ] T002 [US1] Search and replace `AuthProfileManager` string references with `ProviderProfileManager` across `moonmind/workflows/temporal/` directory (e.g. `workers.py`, `agent_run.py`, `worker_runtime.py`, `service.py`).
- [ ] T003 [US1] Search and replace `auth_profile` string references to `provider_profile` across `moonmind/workflows/temporal/` and `moonmind/workflows/adapters/managed_agent_adapter.py`.
- [ ] T004 [US1] Ensure `TemporalWorkflowType` in `moonmind/workflows/temporal/` refers to `MoonMind.ProviderProfileManager`.

## Phase 4: User Story 2 (Updated Execution Contract)

Goal: Update `AgentExecutionRequest` and `ManagedRuntimeProfile` schema definitions and routing to support Provider Profiles.
Independent Test: Pydantic validation passes when no exact reference UUID is provided but a selector is.

- [ ] T005 [US2] Rename `ManagedAgentAuthProfile` to `ManagedAgentProviderProfile` in `moonmind/schemas/agent_runtime_models.py` and `moonmind/schemas/__init__.py`.
- [ ] T006 [US2] Update `AgentExecutionRequest` in `moonmind/schemas/agent_runtime_models.py` to make `execution_profile_ref` optional and add `profile_selector`.
- [ ] T007 [US2] Modify `ManagedRuntimeProfile` in `moonmind/schemas/agent_runtime_models.py` to support `provider_id` and correct properties.
- [ ] T008 [US2] Find and replace any `ManagedAgentAuthProfile` reference with `ManagedAgentProviderProfile` in `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/activities/oauth_session_activities.py`.

## Phase 5: Polish & Tests

- [ ] T009 [P] Update `tests/` references from `auth_profile` to `provider_profile` where failing.
- [ ] T010 Verify compilation and correct behavior by executing `./tools/test_unit.sh`.
