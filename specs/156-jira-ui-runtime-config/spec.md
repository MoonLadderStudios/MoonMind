# Feature Specification: Jira UI Runtime Config

**Feature Branch**: `156-jira-ui-runtime-config`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 1 using test-driven development of the Jira UI plan. Treat docs/UI/CreatePage.md as the canonical desired-state. Add a Jira UI rollout contract to the dashboard runtime config. Keep this separate from existing backend Jira tool enablement. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| Requirement ID | Source | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` section 3, lines 45-47 | Jira must remain an external instruction source, manual entry must remain first-class when Jira is unavailable, and browser clients must call MoonMind APIs rather than Jira directly. |
| DOC-REQ-002 | `docs/UI/CreatePage.md` section 4, lines 68-70 | The Create page must receive server-generated runtime configuration through the boot payload, and page actions must go through MoonMind APIs. |
| DOC-REQ-003 | `docs/UI/CreatePage.md` section 13, lines 307-313 | Jira integration must not auto-create MoonMind tasks, replace the step editor or presets, change task submission into a Jira-native workflow, or make the browser talk directly to Jira. |
| DOC-REQ-004 | `docs/UI/CreatePage.md` section 14, line 327 | The Create page may expose Jira only when runtime configuration explicitly enables it. |
| DOC-REQ-005 | `docs/UI/CreatePage.md` section 14, lines 331-351 | The runtime contract must include Jira source entries and Jira integration settings for enabled state, default project, default board, and session board memory. |
| DOC-REQ-006 | `docs/UI/CreatePage.md` section 14, lines 356-358 | Jira entry points must depend on `system.jiraIntegration.enabled`; Jira URLs must remain MoonMind API endpoints; browser clients must not embed Jira credentials or Jira-domain knowledge beyond documented response shapes. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Hide Jira UI When Disabled (Priority: P1)

As an operator, I want the Create page runtime configuration to omit Jira browser capabilities unless the Jira Create-page rollout is enabled, so enabling trusted Jira tooling for backend workflows does not automatically expose Jira controls to browser users.

**Why this priority**: The rollout boundary is the core safety requirement for Phase 1. Without it, later Jira browser work could appear in environments that only intended to enable server-side tools.

**Independent Test**: Can be fully tested by building the dashboard runtime configuration with the Jira Create-page rollout disabled and confirming the Jira source and system blocks are absent while the existing Create page configuration remains present.

**Acceptance Scenarios**:

1. **Given** the Jira Create-page rollout is disabled, **When** the dashboard runtime configuration is generated, **Then** no Jira browser endpoint block is present.
2. **Given** backend Jira tool enablement may be configured independently, **When** the Jira Create-page rollout is disabled, **Then** the browser runtime configuration still omits Jira Create-page controls.
3. **Given** Jira browser configuration is omitted, **When** the Create page consumes the runtime configuration, **Then** existing manual task authoring, preset, dependency, runtime, repository, publish, attachment, and scheduling behavior remains unchanged.

---

### User Story 2 - Expose Jira Browser Contract When Enabled (Priority: P1)

As a Create page user in an enabled environment, I want the boot/runtime configuration to advertise the MoonMind-owned Jira browser endpoints and rollout settings, so the browser can discover Jira capability through the same configuration channel as the rest of the Create page.

**Why this priority**: Frontend Jira browsing cannot be built safely until the UI has a stable server-owned contract for endpoint discovery and feature state.

**Independent Test**: Can be fully tested by enabling the Jira Create-page rollout and verifying that the runtime configuration contains the required Jira source templates and system settings.

**Acceptance Scenarios**:

1. **Given** the Jira Create-page rollout is enabled, **When** the dashboard runtime configuration is generated, **Then** it includes endpoint templates for connection verification, projects, project boards, board columns, board issues, and issue detail.
2. **Given** the Jira Create-page rollout is enabled, **When** the dashboard runtime configuration is generated, **Then** it includes a Jira integration system block with an enabled flag.
3. **Given** the Jira browser is exposed through MoonMind-owned endpoints, **When** the browser reads the runtime configuration, **Then** it has no need to call Jira directly or receive raw Jira credentials.

---

### User Story 3 - Surface Operator Defaults (Priority: P2)

As an operator, I want optional default Jira project and board preferences to appear in the Create page runtime configuration only under the Jira UI rollout, so the browser can preselect the intended workspace without hardcoding deployment-specific values.

**Why this priority**: Defaults improve the future browser experience but must remain scoped to the same rollout boundary as the Jira UI itself.

**Independent Test**: Can be fully tested by configuring default Jira project and board values, enabling the rollout, and verifying those values appear in the Jira integration system block.

**Acceptance Scenarios**:

1. **Given** a default Jira project key is configured and the rollout is enabled, **When** runtime configuration is generated, **Then** the Jira integration system block includes that project key.
2. **Given** a default Jira board ID is configured and the rollout is enabled, **When** runtime configuration is generated, **Then** the Jira integration system block includes that board ID.
3. **Given** session memory for the last selected board is configured and the rollout is enabled, **When** runtime configuration is generated, **Then** the Jira integration system block includes whether that session behavior is allowed.

### Edge Cases

- Jira Create-page rollout is disabled while backend Jira tooling is enabled: the browser Jira configuration remains absent.
- Jira Create-page rollout is enabled with no default project or board: the browser Jira configuration is present with empty default values.
- Default project keys provided with inconsistent casing or whitespace are normalized before reaching the browser contract.
- Existing runtime configuration consumers ignore Jira entirely when the Jira block is absent.
- The Jira UI rollout state changes between application starts: the generated runtime configuration reflects the current operator setting.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose Jira Create-page browser capability through the dashboard runtime configuration only when the Jira Create-page rollout is enabled. (Maps: DOC-REQ-004, DOC-REQ-006)
- **FR-002**: The system MUST omit all Jira Create-page browser source and system configuration when the Jira Create-page rollout is disabled. (Maps: DOC-REQ-001, DOC-REQ-004)
- **FR-003**: The Jira Create-page rollout MUST be independent from backend Jira tool enablement, so enabling trusted server-side Jira tools does not by itself expose browser Jira controls. (Maps: DOC-REQ-003, DOC-REQ-004)
- **FR-004**: When enabled, the system MUST publish a Jira browser source contract with entries for connection verification, project listing, board listing for a project, column listing for a board, issue listing for a board, and issue detail lookup. (Maps: DOC-REQ-002, DOC-REQ-005)
- **FR-005**: When enabled, the system MUST publish a Jira integration system contract containing whether the integration is enabled, the default project key, the default board ID, and whether the browser may remember the last selected board during the current session. (Maps: DOC-REQ-005, DOC-REQ-006)
- **FR-006**: The runtime configuration MUST preserve existing Create page behavior and existing non-Jira configuration shape when the Jira Create-page rollout is disabled. (Maps: DOC-REQ-001, DOC-REQ-003)
- **FR-007**: The feature MUST include production runtime code changes; documentation or specification updates alone do not satisfy the requested deliverable.
- **FR-008**: The feature MUST include validation tests covering the disabled state, enabled endpoint contract, and configured default project and board values.
- **FR-009**: The browser-facing Jira contract MUST describe MoonMind-owned endpoints only; it MUST NOT introduce any requirement for the browser to receive Jira credentials or call Jira directly. (Maps: DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-006)
- **FR-010**: Operator-provided default Jira project keys MUST be normalized to a stable project-key format before being surfaced to the browser contract.

### Key Entities

- **Jira UI Rollout**: The operator-controlled state that determines whether the Create page may discover Jira browser capability.
- **Jira Source Contract**: The set of MoonMind-owned endpoint templates the Create page can use in later phases to browse Jira data.
- **Jira Integration Settings**: The browser-visible feature metadata and optional defaults for project, board, and session-memory behavior.
- **Dashboard Runtime Configuration**: The boot-time configuration consumed by the Create page and other Mission Control views.

## Assumptions

- Phase 1 defines and validates the runtime discovery contract only; it does not require implementing the Jira browser API endpoints or frontend Jira browsing UI.
- Existing manual Create page workflows must remain fully usable without Jira configuration.
- Empty default project and board values are valid and mean "no operator default selected."
- Session memory controls only whether the browser may remember the last project or board during the current browser session; it does not imply durable persistence.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With Jira Create-page rollout disabled, runtime configuration generation produces no Jira Create-page source block and no Jira integration system block in 100% of tested cases.
- **SC-002**: With Jira Create-page rollout enabled, runtime configuration generation includes all six required Jira browser endpoint templates and all four required Jira integration settings in 100% of tested cases.
- **SC-003**: Configured default project and board values are reflected in the runtime configuration in 100% of enabled test cases.
- **SC-004**: Existing runtime configuration tests for Temporal sources, task runs, task presets, provider profiles, and attachment policy continue to pass without Jira-specific changes to their expected disabled behavior.
- **SC-005**: The delivered change includes at least one production runtime code change and at least one validation test that would fail if the Jira UI rollout boundary were removed.
