# Feature Specification: Seeded MoonSpec Preset Availability

**Feature Branch**: `093-speckit-preset-seeding`  
**Created**: 2026-03-21  
**Status**: Draft  
**Input**: User description: "Restore the `moonspec-orchestrate` preset so it auto-populates in Mission Control Create. Do not rely on AgentKit. Use a MoonMind preset that translates the `moonspec-orchestrate` skill into ordered preset steps."

## User Story 1 - Default preset appears in Mission Control (Priority: P1)

An operator opens the Create tab in Mission Control and expects the seeded global `moonspec-orchestrate` preset to be available without manually creating catalog rows.

**Why this priority**: The preset is not useful if the catalog seed never reaches the database that backs the UI dropdown.

**Independent Test**: Start the API against an empty database, call startup, and verify the global `moonspec-orchestrate` template row exists with an active latest version.

### Acceptance Scenarios

1. **Given** a fresh database and the preset catalog feature flag enabled, **When** API startup completes, **Then** the global `moonspec-orchestrate` preset exists in `task_step_templates` with an active latest version.
2. **Given** Mission Control loads presets after startup, **When** the Create form fetches global presets, **Then** `moonspec-orchestrate` is returned and can be auto-selected by the existing UI preference logic.

## User Story 2 - Seed sync refreshes outdated preset rows (Priority: P1)

A maintainer updates the YAML seed that represents the canonical `moonspec-orchestrate` preset and expects existing seeded catalog rows to match that YAML on startup.

**Why this priority**: The preset previously evolved independently from the desired skill translation; stale rows would keep serving old behavior.

**Independent Test**: Seed an outdated `moonspec-orchestrate` row, run seed sync, and verify the stored latest version steps/annotations now match the YAML document.

### Acceptance Scenarios

1. **Given** an existing global `moonspec-orchestrate` template row, **When** startup seed sync runs, **Then** the template metadata and active version payload are updated from the YAML seed.
2. **Given** the canonical YAML describes a MoonMind-native step sequence using text instructions and skill calls such as `moonspec-specify`, `moonspec-align`, and `moonspec-verify`, **When** sync completes, **Then** the stored preset matches that step sequence instead of legacy AgentKit-specific behavior.

## Functional Requirements

- **FR-001**: The API service MUST synchronize YAML-backed task preset seeds into the catalog during startup when the task preset catalog feature flag is enabled.
- **FR-002**: Startup synchronization MUST create the global `moonspec-orchestrate` preset when it is missing.
- **FR-003**: Startup synchronization MUST refresh the existing `moonspec-orchestrate` template/version payload from the YAML seed when the row already exists.
- **FR-004**: The synchronized preset MUST preserve the MoonMind-native step translation encoded in `api_service/data/task_step_templates/moonspec-orchestrate.yaml`, including mixed instruction-only and skill-backed steps.
- **FR-005**: Startup synchronization MUST fail soft when preset tables are unavailable, logging the condition instead of aborting application startup.
- **FR-006**: Automated tests MUST cover missing-seed creation, existing-seed refresh, and startup-driven seeding.

## Success Criteria

- **SC-001**: A clean startup against an empty database yields a queryable global `moonspec-orchestrate` preset without manual API calls.
- **SC-002**: A stale `moonspec-orchestrate` row is updated to the YAML-defined steps/annotations during synchronization.
- **SC-003**: Regression tests protect both direct catalog sync behavior and the startup integration path.
