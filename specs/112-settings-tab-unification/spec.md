# Feature Specification: Settings Tab Unification

**Feature Branch**: `112-settings-tab-unification`  
**Created**: 2026-03-28  
**Status**: Implemented  
**Input**: User description: "Merge Workers, Settings, and Secrets into a single Settings surface with subsections, and document the Settings tab."

## User Scenarios & Testing

### User Story 1 - Configure providers from one Settings surface (Priority: P1)

As a Mission Control operator, I want provider profiles and managed secrets in one Settings area, so I can configure runtimes without switching between overlapping top-level tabs.

**Independent Test**: Open `/tasks/settings?section=providers-secrets` and verify provider profiles and secrets are both visible and actionable from the same page.

**Acceptance Scenarios**:

1. **Given** I open Mission Control navigation, **When** I look for configuration surfaces, **Then** I see a top-level `Settings` tab and do not see separate top-level `Secrets` or `Workers` tabs.
2. **Given** I visit the providers and secrets section, **When** I manage a secret or provider profile, **Then** I can do so without navigating to another top-level page.

### User Story 2 - Reach operational controls through Settings (Priority: P1)

As an operator, I want worker pause and resume controls under Settings, so operational controls live in the configuration area rather than beside the primary task workflow.

**Independent Test**: Visit `/tasks/settings?section=operations` and verify the worker controls render and still call the worker pause API endpoints.

**Acceptance Scenarios**:

1. **Given** I need worker controls, **When** I navigate to Settings and open `Operations`, **Then** I can view status, pause, resume, and review recent actions.
2. **Given** I use a legacy workers URL, **When** I visit `/tasks/workers`, **Then** I am redirected to `/tasks/settings?section=operations`.

### User Story 3 - Preserve a coherent legacy path (Priority: P2)

As an existing user following older links, I want legacy secrets and workers URLs to land in the correct Settings subsection, so older bookmarks continue to reach the right surface during the IA change.

**Independent Test**: Load `/tasks/secrets` and `/tasks/workers` and verify each redirects to the correct settings subsection.

**Acceptance Scenarios**:

1. **Given** I open `/tasks/secrets`, **When** the server responds, **Then** I am redirected to `/tasks/settings?section=providers-secrets`.
2. **Given** I open `/tasks/workers`, **When** the server responds, **Then** I am redirected to `/tasks/settings?section=operations`.

## Requirements

### Functional Requirements

- **FR-001**: Mission Control MUST expose a single top-level `Settings` navigation item for configuration and admin controls.
- **FR-002**: Mission Control MUST remove top-level `Secrets` and `Workers` navigation items.
- **FR-003**: The Settings page MUST provide a `Providers & Secrets` subsection containing provider-profile configuration and managed secret management.
- **FR-004**: The Settings page MUST provide an `Operations` subsection containing worker pause and resume controls plus recent audit actions.
- **FR-005**: The Settings page MUST provide a `User / Workspace` subsection reserved for broader operator and workspace configuration.
- **FR-006**: Legacy routes `/tasks/secrets` and `/tasks/workers` MUST redirect to the appropriate Settings subsection.
- **FR-007**: Mission Control documentation MUST define the purpose and layout of the unified Settings tab.

## Success Criteria

- **SC-001**: Operators can reach provider profiles, secrets, and worker controls from `/tasks/settings` without using separate top-level tabs.
- **SC-002**: Legacy visits to `/tasks/secrets` and `/tasks/workers` resolve to Settings subsection URLs with HTTP redirects.
- **SC-003**: The Settings tab documentation describes the desired-state subsection model for providers, secrets, user/workspace config, and operations.
