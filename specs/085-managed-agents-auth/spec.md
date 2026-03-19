# Feature Specification: Managed Agents Authentication

**Feature Branch**: `001-managed-agents-auth`  
**Created**: 2026-03-14  
**Status**: Draft  
**Input**: User description: "Finish implementing docs/ManagedAgents/ManagedAgentsAuthentication.md"

## Source Document Requirements

- **DOC-REQ-001**: [Sec 2: Environment Shaping] When OAuth mode is active, the auth system must explicitly clear API-key environment variables so the CLI does not silently fall back to key-based auth.
- **DOC-REQ-002**: [Sec 4: Auth Profile Registry] The system must map one auth volume to one runtime family on one worker using a ManagedAgentAuthProfile registry.
- **DOC-REQ-003**: [Sec 4: Volume Naming] When multiple OAuth volumes exist for the same runtime, each volume is named with a distinguishing suffix.
- **DOC-REQ-004**: [Sec 5: Singleton Resource Manager] Each managed agent runtime family gets its own long-lived manager workflow instance (`auth-profile-manager:<runtime_id>`).
- **DOC-REQ-005**: [Sec 5: Profile Assignment] AuthProfileManager evaluates available profiles (enabled, not in cooldown, available_slots > 0) and selects the profile with the most free slots.
- **DOC-REQ-006**: [Sec 5: Waiting for Available Profiles] If all eligible profiles are at capacity or in cooldown, the manager queues the request in FIFO order.
- **DOC-REQ-007**: [Sec 5: Cooldown After 429] When a managed runtime encounters 429 RESOURCE_EXHAUSTED, the AgentRun signals the manager to mark the profile as in cooldown for the specified duration and releases the slot.
- **DOC-REQ-008**: [Sec 5: Continue-As-New] After 2000 events, the manager serializes its current state and restarts with that state as input using continue-as-new.
- **DOC-REQ-009**: [Sec 6: Volume Mounting] The ManagedRuntimeLauncher ensures the profile's volume_ref is accessible to the execution environment.
- **DOC-REQ-010**: [Sec 7: Profile Persistence] Auth profiles must be stored in the managed_agent_auth_profiles database table.
- **DOC-REQ-011**: [Sec 7: Runtime State] Transient concurrency state (current_parallel_runs, cooldown_until, consecutive_429_count) is tracked in Temporal workflow layer, not the database.
- **DOC-REQ-012**: [Sec 9: Security Considerations] Auth profiles are referenced by profile_id, never by token or key value.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Multi-Account Authentication Setup (Priority: P1)

System administrators configure multiple authentication accounts for managed UI agents (e.g., using different subscription tiers or service accounts), enabling per-account resource caps and distinct rate limits.

**Why this priority**: Core enabler for rate limiting and subscription-tier tracking.

**Independent Test**: Can be validated by successfully provisioning two distinct volumes for the same runtime family via the `auth` shell scripts and registering them in the system database.

**Acceptance Scenarios**:

1. **Given** multiple available OAuth accounts, **When** the admin provisions different volumes using the auth script with unique suffixes, **Then** distinct volumes are created successfully.
2. **Given** provisioned volumes, **When** they are registered in the auth profiles table, **Then** they appear as selectable execution profiles.

---

### User Story 2 - Profile-Aware Execution and Rate Limiting (Priority: P1)

When a managed agent is executed, the workflow dynamically leases an available auth profile and applies environment shaping, mitigating unexpected fallback auth behavior. If rate-limited, the system correctly cools down the profile and finds another one or waits.

**Why this priority**: Required for production reliability and failover when working with strict provider quotas.

**Independent Test**: Can be validated by running multiple parallel agents under a single runtime family and observing them bind to separate slots or queue correctly during cooldown.

**Acceptance Scenarios**:

1. **Given** an AgentExecutionRequest, **When** the `AgentRun` workflow starts, **Then** it signals `AuthProfileManager` to lease a profile and uses the assigned `volume_ref` and shaped environment.
2. **Given** a 429 RESOURCE_EXHAUSTED error during execution, **When** the `AgentRun` catches the error, **Then** it signals `report_cooldown` to the manager to sideline that profile.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: API key environment variables MUST be explicitly cleared in the shaped environment when the assigned profile uses OAuth auth_mode. (Maps to DOC-REQ-001)
- **FR-002**: The `AuthProfileManager` singleton workflow MUST be implemented per managed runtime family to broker available assignment slots. (Maps to DOC-REQ-002, DOC-REQ-004)
- **FR-003**: The environment configuration MUST resolve the selected profile's `volume_mount_path` for the runtime dynamically rather than hardcoding. (Maps to DOC-REQ-003, DOC-REQ-009)
- **FR-004**: The `AuthProfileManager` MUST select an enabled, non-cooldown profile with the highest number of available slots. (Maps to DOC-REQ-005)
- **FR-005**: The `AuthProfileManager` MUST durably queue slot requests in FIFO order if no profiles have immediate availability. (Maps to DOC-REQ-006)
- **FR-006**: Workflows MUST signal `report_cooldown` to the `AuthProfileManager` on `429` errors to suspend scheduling for a duration. (Maps to DOC-REQ-007)
- **FR-007**: The `AuthProfileManager` MUST invoke continue-as-new after processing 2000 events to manage history limits. (Maps to DOC-REQ-008)
- **FR-008**: Auth profiles MUST be stored persistently via a `managed_agent_auth_profiles` PostgreSQL table. (Maps to DOC-REQ-010)
- **FR-009**: The runtime concurrency limits and active lease tracking MUST reside strictly within Temporal states. (Maps to DOC-REQ-011)
- **FR-010**: All secrets/tokens MUST be retrieved separately at runtime, utilizing `profile_id` as the only durable reference. (Maps to DOC-REQ-012)

### Edge Cases

- What happens if the `AuthProfileManager` crashes? (Temporal durably resumes state; workflows wait for signals upon recovery).
- How does the system handle an API key profile whose key is later deleted from the secret store? (Fails execution explicitly; does not cascade to cooldown unless it's a 429).
- What happens if all profiles are sidelined on cooldown? (AgentRun waits durably in queue until the first cooldown expires).

### Key Entities

- **AuthProfileManager Workflow**: The singleton broker orchestrating profile assignment per runtime family.
- **ManagedAgentAuthProfile**: The database record storing the static configuration and limits for an agent identity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of managed agent runs successfully lease a profile slot via the `AuthProfileManager`.
- **SC-002**: Workflows never exceed the concurrency boundaries defined in the profiles configuration.
- **SC-003**: 100% of 429 HTTP responses trigger a cooldown signal that sidelines the specific profile for subsequent tasks without breaking the main queue.
- **SC-004**: Execution environment has 0 inadvertent API keys exported when using OAuth profiles.
