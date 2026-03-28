# Tasks: provider-profiles-p3

## Phase 1: Models & Schemas

- `[x]` T001: Update `AgentExecutionRequest` and `AgentRunInput` to make `execution_profile_ref` optional and fully remove/deprecate `"auto"` sentinel values.
- `[x]` T006: Clean up `agent_runtime_models.py` definitions and perform broad audit of remaining `AuthProfileManager` or "auto" references across tests and workflows.

## Phase 2: Workflow Routing & Selection

- `[x]` T002: Upgrade `run.py` to natively map lack of `execution_profile_ref` into generic `profile_selector` evaluations in `ManagedAgentAdapter`.
- `[x]` T003: Update `managed_agent_adapter.py` to route workload slot allocations using strictly defined tags/provider logic when fallback routing.
- `[x]` T004: Enhance `ProviderProfileManagerWorkflow` to evaluate selector constraints (`provider_id`, `tags_all`, etc.) during queue drainage.

## Phase 3: AgentRun Snapshots

- `[x]` T005: Make `AgentRun` properly fetch and utilize full `ManagedRuntimeProfile` properties throughout cooldown processing and retry sequences (snapshots).

## Validation

- `[x]` T007: Execute comprehensive unit testing suite `tools/test_unit.sh`.
