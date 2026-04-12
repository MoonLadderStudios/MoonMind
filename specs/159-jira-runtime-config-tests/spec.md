# Feature Specification: Jira Runtime Config Tests

**Feature Branch**: `159-jira-runtime-config-tests`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 3 using test-driven development of the Jira UI plan. Goal: make the Create page discover Jira capability through the same boot payload pattern it already uses. Work includes updating dashboard runtime config to publish Jira UI endpoints and flags, adding tests for disabled state, enabled endpoint templates, and configured board/project defaults, treating docs/UI/CreatePage.md as the canonical desired-state, preserving runtime intent, and requiring production runtime code changes plus validation tests."

## Source Document Requirements

| Requirement ID | Source | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/UI/CreatePage.md` section 3, lines 45-47 | Jira must remain an external instruction source, manual entry must remain first-class when Jira is unavailable, and browser clients must call MoonMind APIs rather than Jira directly. |
| DOC-REQ-002 | `docs/UI/CreatePage.md` section 4, lines 67-70 | The Create page must receive server-generated runtime configuration through the boot payload, and page actions must go through MoonMind REST APIs. |
| DOC-REQ-003 | `docs/UI/CreatePage.md` section 14, line 327 | The Create page may expose Jira only when runtime configuration explicitly enables it. |
| DOC-REQ-004 | `docs/UI/CreatePage.md` section 14, lines 331-351 | The runtime contract must include Jira source entries and Jira integration settings for enabled state, default project, default board, and session board memory. |
| DOC-REQ-005 | `docs/UI/CreatePage.md` section 14, lines 356-358 | Jira entry points must depend on the Jira integration enabled setting; Jira URLs must remain MoonMind API endpoints; browser clients must not embed Jira credentials or Jira-domain knowledge beyond documented response shapes. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Keep Jira Hidden When Disabled (Priority: P1)

As an operator, I want the Create page boot payload to omit Jira browser capability when the Jira Create-page rollout is disabled, so manual task creation and existing Create-page behavior remain unchanged in environments that have not opted in.

**Why this priority**: This is the core rollout guard for Phase 3. The UI must not discover Jira browser controls unless the operator intentionally exposes them.

**Independent Test**: Can be tested by generating the Create page runtime configuration with Jira UI disabled and confirming that no Jira source block or Jira integration system block is present while the existing non-Jira configuration remains available.

**Acceptance Scenarios**:

1. **Given** the Jira Create-page rollout is disabled, **When** the Create page boot payload is generated, **Then** it contains no Jira browser source configuration.
2. **Given** the Jira Create-page rollout is disabled, **When** the Create page boot payload is generated, **Then** it contains no Jira integration system settings.
3. **Given** Jira browser configuration is absent, **When** the Create page consumes the boot payload, **Then** manual task authoring, presets, dependencies, runtime selection, repository settings, publishing, attachments, scheduling, and submission remain available as before.

---

### User Story 2 - Publish Jira Discovery Contract When Enabled (Priority: P1)

As a Create page user in an enabled environment, I want the boot payload to advertise the Jira browser endpoints and feature state, so the page can discover Jira capability through the same runtime configuration pattern it already uses for other Create-page services.

**Why this priority**: Later frontend Jira browsing depends on a stable server-provided discovery contract. This Phase 3 slice provides that contract without requiring the browser to know Jira credentials or Jira service details.

**Independent Test**: Can be tested by enabling the Jira Create-page rollout and confirming the runtime configuration includes the required Jira endpoint templates and an enabled Jira integration system block.

**Acceptance Scenarios**:

1. **Given** the Jira Create-page rollout is enabled, **When** the Create page boot payload is generated, **Then** it includes Jira endpoint templates for connection verification, project listing, board listing, board columns, board issues, and issue detail.
2. **Given** the Jira Create-page rollout is enabled, **When** the Create page boot payload is generated, **Then** it includes Jira integration settings with `enabled` set to true.
3. **Given** the boot payload exposes Jira browser discovery, **When** the browser reads it, **Then** it can rely only on MoonMind-owned endpoints and does not need raw Jira credentials or direct Jira-domain calls.

---

### User Story 3 - Surface Operator Defaults (Priority: P2)

As an operator, I want configured default Jira project and board preferences to appear in the enabled Create page boot payload, so the future Jira browser can preselect the intended workspace without hardcoding deployment-specific values.

**Why this priority**: Defaults are not required to enforce the rollout boundary, but they are part of the agreed runtime contract and improve the later browser experience.

**Independent Test**: Can be tested by configuring default Jira project and board values, enabling the Jira Create-page rollout, and confirming those values are surfaced in the Jira integration settings.

**Acceptance Scenarios**:

1. **Given** a default Jira project key is configured and the rollout is enabled, **When** the Create page boot payload is generated, **Then** the Jira integration settings include that project key.
2. **Given** a default Jira board ID is configured and the rollout is enabled, **When** the Create page boot payload is generated, **Then** the Jira integration settings include that board ID.
3. **Given** session memory for the last selected board is configured and the rollout is enabled, **When** the Create page boot payload is generated, **Then** the Jira integration settings include whether session-only board memory is allowed.

### Edge Cases

- Jira Create-page rollout is disabled while trusted backend Jira tooling is enabled: the browser discovery contract remains absent.
- Jira Create-page rollout is enabled with no default project or board: the discovery contract is present with empty default values.
- Default project or board values contain surrounding whitespace: the values surfaced to the browser are normalized consistently.
- Existing Create-page boot payload consumers do not fail when Jira configuration is absent.
- The operator changes the Jira UI rollout setting between application starts: newly generated boot payloads reflect the current rollout state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose Jira browser capability through the Create page runtime configuration only when the Jira Create-page rollout is enabled. (Maps: DOC-REQ-002, DOC-REQ-003, DOC-REQ-005)
- **FR-002**: The system MUST omit all Jira browser source and Jira integration settings when the Jira Create-page rollout is disabled. (Maps: DOC-REQ-001, DOC-REQ-003)
- **FR-003**: Jira UI discovery MUST remain independent from trusted backend Jira tool enablement, so enabling backend Jira tooling does not automatically expose Create page Jira browser controls. (Maps: DOC-REQ-001, DOC-REQ-003, DOC-REQ-005)
- **FR-004**: When Jira UI discovery is enabled, the system MUST publish browser source entries for connection verification, projects, boards, columns, issues, and issue detail. (Maps: DOC-REQ-004, DOC-REQ-005)
- **FR-005**: When Jira UI discovery is enabled, the system MUST publish Jira integration settings for enabled state, default project key, default board ID, and whether the browser may remember the last selected board during the current session. (Maps: DOC-REQ-004, DOC-REQ-005)
- **FR-006**: The system MUST preserve the existing Create page runtime configuration shape and behavior for non-Jira sources, provider profiles, templates, attachment policy, dependencies, scheduling, and submission. (Maps: DOC-REQ-001, DOC-REQ-002)
- **FR-007**: The browser-facing Jira discovery contract MUST describe MoonMind-owned endpoints only and MUST NOT require browser access to Jira credentials or direct Jira service calls. (Maps: DOC-REQ-001, DOC-REQ-005)
- **FR-008**: The feature MUST include production runtime code changes; documentation or specification updates alone do not satisfy this runtime-mode deliverable.
- **FR-009**: The feature MUST include validation tests for disabled Jira UI configuration, enabled endpoint discovery, configured default project and board values, and separation from backend Jira tool enablement.
- **FR-010**: Validation tests MUST continue to cover existing Create page runtime configuration behavior so Jira UI discovery remains additive.

### Key Entities

- **Create Page Runtime Configuration**: The boot-time configuration consumed by the Create page to discover available services, defaults, and feature settings.
- **Jira UI Rollout**: The operator-controlled state that determines whether Jira browser capability may appear in the Create page boot payload.
- **Jira Browser Source Contract**: The browser-visible set of MoonMind-owned endpoint templates used by later phases to browse Jira data.
- **Jira Integration Settings**: The browser-visible settings describing enabled state, optional defaults, and session-only board memory behavior.
- **Trusted Backend Jira Tooling**: Existing server-side Jira capability that may be enabled independently from the Create page Jira browser.

## Assumptions

- Phase 3 covers runtime configuration exposure and test coverage only; it does not require building the Jira browser UI or server-side board-browser endpoints.
- Empty default project and board values are valid and mean no operator default is selected.
- Session memory controls only browser-session preference restoration and does not imply durable persistence.
- Existing Create page manual task creation remains the fallback and must stay usable whether Jira UI discovery is enabled or disabled.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of tested disabled-rollout cases, the Create page boot payload contains no Jira source block and no Jira integration system block.
- **SC-002**: In 100% of tested enabled-rollout cases, the Create page boot payload contains all six required Jira browser source entries and all four required Jira integration settings.
- **SC-003**: In 100% of tested configured-default cases, default project key, default board ID, and session board memory values are surfaced correctly when the rollout is enabled.
- **SC-004**: At least one validation test fails if Jira UI discovery becomes coupled to backend Jira tool enablement.
- **SC-005**: Existing runtime configuration tests for non-Jira Create page capabilities continue to pass after the runtime code change.
- **SC-006**: The delivered change includes production runtime code changes and validation tests; spec-only or documentation-only output is not accepted.
