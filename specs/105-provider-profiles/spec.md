# Feature Specification: Provider Profiles Migration

**Feature Branch**: `105-provider-profiles`  
**Created**: 2026-03-26  
**Status**: Draft  
**Input**: User description: "Update the auth profile system to the more robust Provider Profiles system described in docs/Security/ProviderProfiles.md"

## Source Document Requirements

Extracted from `docs/Security/ProviderProfiles.md`:

- **DOC-REQ-001** *(§2 Goals, §5.1 Canonical Contract)*: The system must support multiple independent profiles for the same runtime and provider, defined by a unified schema with explicit credential sources (`oauth_volume`, `secret_ref`, `none`) and materialization modes (`oauth_home`, `api_key_env`, `env_bundle`, `config_bundle`, `composite`).
- **DOC-REQ-002** *(§11.1 Table, §13.2 Migration Strategy)*: The system must atomically migrate the database table from `managed_agent_auth_profiles` to `managed_agent_provider_profiles` (with new columns like `provider_id`) and rename the singleton manager workflow to `ProviderProfileManager`.
- **DOC-REQ-003** *(§8.2 Request Contract, §8.3 Resolution Order)*: `AgentExecutionRequest` must accept a `profile_selector` (containing `provider_id`, tags), and the manager must resolve this by filtering eligible profiles and selecting the highest `priority` one, breaking ties by available slots.
- **DOC-REQ-004** *(§10 Runtime Materialization Pipeline, §12.5 Clear Competing Variables)*: The runtime launcher must materialize the environment via a strict layering order that does not completely overwrite the base environment, explicitly clears `clear_env_keys`, and resolves secrets immediately prior to launch.
- **DOC-REQ-005** *(§12.1, §12.2, §12.6 Secrets)*: Secrets must never be stored directly in the profile table or workflow payloads. They must only exist as references (`secret_ref`) that are resolved at runtime in memory.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Provider-Aware Agent Dispatch (Priority: P1)

As a workflow or API caller requesting an agent execution, I can specify that I want a `claude_code` agent using `provider_id: minimax` instead of just asking for a generic `claude_code` agent, so that my task is executed by the correct third-party provider.

**Why this priority**: Without provider-level dispatch filtering, multi-provider runtimes will non-deterministically route tasks to whichever provider has an open slot, causing unintended vendor usage and potentially failing tasks.

**Independent Test**: Can be fully tested by submitting a run request with a specific `provider_id` selector and verifying that the `ProviderProfileManager` only assigns slots from profiles matching that provider.

**Acceptance Scenarios**:

1. **Given** multiple available `claude_code` profiles (Anthropic, MiniMax), **When** a run requests `provider_id: minimax`, **Then** the manager assigns a MiniMax profile slot.
2. **Given** a request with an explicit `execution_profile_ref`, **When** the workflow starts, **Then** it bypasses selection and uses the exact requested profile.

---

### User Story 2 - Environment Variables Layering (Priority: P1)

As an operator, I can configure a profile with an `env_template`, `home_path_overrides`, and `clear_env_keys`, so that the system correctly constructs the specific environment needed for that provider without wiping out essential system paths like `PATH`.

**Why this priority**: Required for correctly configuring third-party providers (like MiniMax) that need Anthropic-compatible environment variables to be injected while explicitly removing standard Anthropic API keys.

**Independent Test**: Can be fully tested by launching an agent with a composite profile and asserting on the final environment variable dictionary passed to the sub-process.

**Acceptance Scenarios**:

1. **Given** a profile with `clear_env_keys` containing `ANTHROPIC_API_KEY`, **When** the launcher prepares the environment, **Then** `ANTHROPIC_API_KEY` is not present in the final subprocess environment.
2. **Given** a profile with `env_template`, **When** the launcher prepares the environment, **Then** these keys are overlaid onto the system base env rather than replacing it.

---

### User Story 3 - Launch-Time Secret Resolution (Priority: P2)

As an operator, I can configure provider keys using `secret_ref` strings instead of raw keys, so that highly sensitive API keys are not exposed in the database or workflow execution history.

**Why this priority**: Core security requirement to prevent credential leakage.

**Independent Test**: Can be independently verified by inspecting the database rows and Temporal event history to ensure no raw secrets are present.

**Acceptance Scenarios**:

1. **Given** a profile with an API key mapped in `env_template` via a `secret_ref`, **When** the launcher builds the environment, **Then** the secret is fetched from secret storage and injected into the subprocess environment.
2. **Given** an executed workflow, **When** reviewing the Temporal history payloads, **Then** the `secret_ref` string is visible, but the raw secret is never present in the payload.

### Edge Cases

- What happens when a request specifies a `provider_id` but all matching profiles are disabled? (Should wait gracefully or return a clear error, depending on cooldown state)
- How does system handle resolving a `secret_ref` that doesn't exist in the secret store during launch? (Should fail the activity loudly and not launch the process)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST migrate the database schema from `managed_agent_auth_profiles` to `managed_agent_provider_profiles` with the new schema fields. (Maps to DOC-REQ-002)
- **FR-002**: System MUST rename the `AuthProfileManager` singleton workflow to `ProviderProfileManager` across all workflow definitions and references. (Maps to DOC-REQ-002)
- **FR-003**: `AgentExecutionRequest` MUST accept a `profile_selector` object with `provider_id`, `tags_all`, and `tags_any`. (Maps to DOC-REQ-003, DOC-REQ-001)
- **FR-004**: The profile manager MUST implement the documented resolution order, filtering by runtime and provider, then sorting by `priority` descending and breaking ties by available slots. (Maps to DOC-REQ-003)
- **FR-005**: The `ManagedRuntimeLauncher` MUST prepare the runtime environment by strictly layering variables, removing keys listed in `clear_env_keys`, and merging `env_template` and `home_path_overrides`. (Maps to DOC-REQ-004)
- **FR-006**: The system MUST store only `secret_ref` pointers in the database and workflow payloads, resolving the actual sensitive values only immediately before `subprocess.run` execution. (Maps to DOC-REQ-005)

### Key Entities *(include if feature involves data)*

- **Provider Profile**: A persistent database record that defines how a specific runtime connects to a specific provider (e.g., credentials, environment templates).
- **Profile Selector**: A parameter object passed during task submission to identify which provider/tag profile should be used for the run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All existing auth profiles are successfully migrated to provider profiles with no loss of authentication capability.
- **SC-002**: A `claude_code` task can be successfully dispatched to a MiniMax provider profile using a `provider_id: minimax` selector without accidentally landing on an Anthropic profile.
- **SC-003**: Temporal workflow history for an agent run contains exactly 0 occurrences of a raw provider API key.
- **SC-004**: The system can successfully launch an agent using the new `env_bundle` materialization mode while preserving standard system variables like `PATH`.

## Traceability Matrix

| DOC-REQ ID   | FR ID     | Validation Strategy                                         | Description                      |
| ------------ | --------- | ----------------------------------------------------------- | -------------------------------- |
| DOC-REQ-001  | FR-003    | `test_create_auth_profile`                                  | Profile creation & modes         |
| DOC-REQ-002  | FR-001    | `test_auto_seed_creates_default_profiles`                   | Schema migration & renamed IDs   |
| DOC-REQ-002  | FR-002    | `test_update_profile_syncs_auth_profile_manager`            | Profile Manager integration      |
| DOC-REQ-003  | FR-003, 4 | `test_auth_profile_list_filters_by_runtime_id`              | Profile filtering and sorting    |
| DOC-REQ-004  | FR-005    | `test_launch_env_overrides_layer_on_top_of_os_environ`      | Env layering & key execution     |
| DOC-REQ-005  | FR-006    | `test_auth_profile_list_returns_empty_for_unknown_runtime`  | Secret refs and payload hiding   |
