# Feature Specification: Codex CLI OpenRouter Phase 2

**Feature Branch**: `126-codex-openrouter-phase2`
**Created**: 2026-04-03
**Status**: Draft
**Input**: User description: "Fully implement Phase 2 from docs\ManagedAgents\CodexCliOpenRouter.md"

## Source Document Requirements

- **DOC-REQ-006**: Phase 2 must enable Mission Control creation/editing for OpenRouter Codex profiles through the generic provider-profile management UI, including validation for openrouter-specific fields. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §15 Phase 2.
- **DOC-REQ-007**: Phase 2 must verify `profile_selector.provider_id = openrouter` dynamic routing through the provider-profile manager, ensuring requests with `{"profileSelector": {"providerId": "openrouter"}}` correctly resolve to the highest-priority enabled codex_cli + openrouter profile. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §9.2, §15 Phase 2.
- **DOC-REQ-008**: Phase 2 must add strategy support for suppressing redundant default `-m` when the provider profile already defines the default via generated config, while honoring explicit request model overrides. **NOTE: This was already implemented in Phase 1. Phase 2 verifies the implementation works end-to-end but requires no new code changes.** Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §11.4, §14.1, §15 Phase 2.
- **DOC-REQ-009**: Phase 2 must add integration coverage for cooldown and slot behavior specific to the openrouter provider profile, verifying that cooldown attaches to the openrouter profile rather than all codex_cli runs globally. Source: `docs/ManagedAgents/CodexCliOpenRouter.md` §14.2, §15 Phase 2.

## Requirements Mapping

- **FR-006**: Mission Control MUST support creating, editing, and managing OpenRouter Codex profiles through the provider-profile management interface with appropriate validation for openrouter-specific fields. (Maps to DOC-REQ-006)
- **FR-007**: Dynamic routing via `profile_selector.provider_id = openrouter` MUST resolve to the correct codex_cli + openrouter profile with integration test coverage. (Maps to DOC-REQ-007)
- **FR-008**: CodexCliStrategy MUST honor `command_behavior.suppress_default_model_flag` to omit redundant `-m` when config already supplies the default, while preserving explicit override behavior. (Maps to DOC-REQ-008)
- **FR-009**: Integration tests MUST verify that cooldown and slot leasing attach to the openrouter provider profile specifically, not globally to all codex_cli runs. (Maps to DOC-REQ-009)

## User Stories & Validation

### User Story 1 - Manage OpenRouter Codex profiles through Mission Control (P2)

Operators need to create, edit, and manage OpenRouter Codex provider profiles through the Mission Control UI without requiring manual API calls or database edits.

**Independent Test**: Use Mission Control to create an OpenRouter Codex profile, edit its settings, and verify the profile persists correctly with all openrouter-specific fields.

**Acceptance Scenarios**:

1. **Given** the provider-profile management UI, **when** a user creates a profile with `provider_id=openrouter` and `runtime_id=codex_cli`, **then** the profile is created with appropriate validation for openrouter-specific fields.
2. **Given** an existing OpenRouter profile, **when** a user edits its cooldown or model settings, **then** changes persist and reflect in subsequent launches.

### User Story 2 - Dynamic routing via provider_id=openrouter (P2)

Operators need to launch managed Codex runs against OpenRouter using dynamic provider-aware routing instead of requiring exact profile IDs.

**Independent Test**: Submit a managed run request with `{"profileSelector": {"providerId": "openrouter"}}` and verify it resolves to the correct codex_cli + openrouter profile.

**Acceptance Scenarios**:

1. **Given** multiple provider profiles exist, **when** a request uses `profile_selector.provider_id = openrouter`, **then** the highest-priority enabled codex_cli + openrouter profile is selected.
2. **Given** no openrouter profile exists, **when** a request uses `profile_selector.provider_id = openrouter`, **then** an appropriate error is returned.

### User Story 3 - Suppress redundant default model flag (P2)

Codex runs should not include redundant `-m` flags when the generated config already supplies the default model through the provider profile.

**Independent Test**: Unit-test `CodexCliStrategy.build_command()` with `suppress_default_model_flag=true` and verify command omits `-m` for default but includes it for explicit overrides.

**Acceptance Scenarios**:

1. **Given** a profile with `suppress_default_model_flag=true`, **when** no explicit model override is provided, **then** the command omits `-m`.
2. **Given** the same profile, **when** an explicit model override is provided, **then** the command includes `-m <override>`.

### User Story 4 - Integration coverage for openrouter cooldown and slot behavior (P2)

OpenRouter provider profiles must have dedicated integration test coverage for cooldown and slot behavior, ensuring these policies attach to the specific profile rather than globally.

**Independent Test**: Run integration tests that launch codex_cli runs with openrouter profiles and verify cooldown/slot behavior attaches to the openrouter profile specifically.

**Acceptance Scenarios**:

1. **Given** an openrouter provider profile with cooldown policy, **when** a run triggers cooldown, **then** the cooldown attaches to the openrouter profile, not all codex_cli runs.
2. **Given** an openrouter profile with slot leasing, **when** a run leases a slot, **then** the slot is tracked against the openrouter profile specifically.

## Success Criteria

- **SC-004**: Mission Control can create, edit, and manage OpenRouter Codex profiles with appropriate field validation.
- **SC-005**: Dynamic routing via `profile_selector.provider_id = openrouter` works correctly with integration test coverage.
- **SC-006**: `suppress_default_model_flag` is implemented and tested at the Codex strategy boundary.
- **SC-007**: Integration tests exist that verify cooldown and slot behavior attach to openrouter provider profiles specifically.
- **SC-008**: All `DOC-REQ-*` items have implementation and validation task coverage in `tasks.md`.
