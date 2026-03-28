# Feature Specification: Provider Profiles Phase 1 Migration

**Feature Branch**: `109-provider-profiles-p1`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Fully implement Phase 1 of docs/tmp/005-ProviderProfilesPlan.md"

## Source Document Requirements

Extracted from `docs/tmp/005-ProviderProfilesPlan.md`:

- **DOC-REQ-001** *(§6.3.A)*: Rename all code symbols and files from `AuthProfileManager` to `ProviderProfileManager`, including `auth_profile_service.py`, `MoonMind.AuthProfileManager`, temporal task names, activity prefixes, docstrings, and logger messages.
- **DOC-REQ-002** *(§6.3.B)*: Align Pydantic and runtime contracts by replacing `ManagedAgentAuthProfile` with `ManagedAgentProviderProfile` runtime schemas, updating `ManagedRuntimeProfile` shape, removing obsolete sentinel values (`"auto"`), and normalizing `AgentExecutionRequest` to accept `profile_selector` while making `execution_profile_ref` an optional exact-reference.
- **DOC-REQ-003** *(§6.3.C)*: Update enums and workflow catalog models, specifically `TemporalWorkflowType`, search parameters, dashboard filters, and labels to use the updated Provider Profile nomenclature without `auth_profile` terminology.
- **DOC-REQ-004** *(§6.4)*: Write unit tests covering the updated Pydantic schemas, exact-profile reference / selector-only requests, and guarantee that no raw secret-like values are accepted into the runtime contracts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Renamed Subsystem Semantics (Priority: P1)

As a developer tracing an agent execution flow, I can read through the service layer, manager workflow, and temporal dashboard using "Provider Profile" terminology rather than a confusing mix of legacy "Auth Profile" names, making it easier to understand the system and onboard new team members.

**Why this priority**: Required first step before modifying behavior, to avoid maintaining dual terminology inside single systems.

**Independent Test**: Can be validated by successfully compiling/checking types after the global rename and asserting that the `agent_runtime` stack boots without `ModuleNotFoundError` or validation mismatches on startup.

**Acceptance Scenarios**:

1. **Given** a new agent execution request, **When** the temporal task is scheduled, **Then** the workflow type created is `MoonMind.ProviderProfileManager`.
2. **Given** a temporal events history, **When** inspecting activities fired during run selection, **Then** none of the activity names contain `auth_profile`.

---

### User Story 2 - Updated Execution Contract (Priority: P1)

As a client (API or Agent), I can submit a request with `execution_profile_ref` omitted and instead provide a `profile_selector`, allowing the backend to deterministically route the execution to an available instance of that provider profile class rather than me needing to look up precise UUIDs.

**Why this priority**: Shifting from deterministic ID launching to capability-based routing is a prerequisite for multi-provider strategies.

**Independent Test**: Run a unit/integration test submitting an API request without `executionProfileRef` but supplying `providerId: minimax`. Validate that the request passes Pydantic constraints and triggers manager routing.

**Acceptance Scenarios**:

1. **Given** a request missing `execution_profile_ref`, **When** the route processes the payload, **Then** Pydantic validates successfully if `profile_selector` is populated.
2. **Given** a legacy request including `"execution_profile_ref": "auto"`, **When** validated against the new contract, **Then** it cleanly errors or migrates.

### Edge Cases

- What happens to already running `AuthProfileManager` instances for live runs? (We cannot rename the ID of running workflows without breaking deterministic replay. We may need to finish those runs or provide runtime-compatibility for names, but since it's pre-release, we prefer hard-failing in-flight runs to introducing translation aliases for in-flight tasks unless forbidden).
- How do we handle dashboard metrics built around the old `MoonMind.AuthProfileManager` string? (They will start anew for the new name).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST process python renames: `auth_profile_service.py` -> `provider_profile_service.py` and references.
- **FR-002**: System MUST process pydantic schema renames from `ManagedAgentAuthProfile` to `ManagedAgentProviderProfile`.
- **FR-003**: System MUST update `AgentExecutionRequest` to make `execution_profile_ref` optional.
- **FR-004**: System MUST rename the temporal workflow definition name for profile management to `MoonMind.ProviderProfileManager`.
- **FR-005**: System MUST rename all temporal activity constants from `auth_profile.*` to `provider_profile.*`.
- **FR-006**: System MUST not allow raw secret values into the runtime contracts (via explicit Pydantic exclusion or omission in the new structures).
- **FR-007**: System MUST provide validation tests proving selector acceptance via Pydantic.

### Key Entities

- **AgentExecutionRequest**: The Pydantic request shape containing the selector.
- **ProviderProfileManager**: The new name for the manager temporal workflow.
- **Provider Profile Service**: The backend service handling database actions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `grep -ri "auth_profile" backend/` returns 0 results in the active profile manager directories (excluding migrations/docs where expected).
- **SC-002**: `pytest backend/tests/unit` passes 100%.
- **SC-003**: Workflows can successfully be started with the new `MoonMind.ProviderProfileManager` name.
