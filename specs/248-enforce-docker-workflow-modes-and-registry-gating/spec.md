# Feature Specification: Enforce Docker Workflow Modes and Registry Gating

**Feature Branch**: `248-enforce-docker-workflow-modes-and-registry-gating`  
**Created**: 2026-04-24  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-499 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-499-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched MM-499 under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-499 MoonSpec Orchestration Input

## Source

- Jira issue: MM-499
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce Docker workflow modes and registry gating
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-499 from MM project
Summary: Enforce Docker workflow modes and registry gating
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-499 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-499: Enforce Docker workflow modes and registry gating

Source Reference
- Source Document: `docs/ManagedAgents/DockerOutOfDocker.md`
- Source Title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
- Source Sections:
  - 1. Purpose
  - 2. Core decisions
  - 6. Docker workflow permission modes
  - 19. Stable design rules
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-003
  - DESIGN-REQ-007
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-010
  - DESIGN-REQ-011

User Story
As a deployment operator, I can set Docker workflow access to disabled, profiles, or unrestricted so MoonMind exposes and enforces only the workload tools allowed for that environment.

Acceptance Criteria
- Given `MOONMIND_WORKFLOW_DOCKER_MODE` is omitted, when settings load, then the effective mode is `profiles`.
- Given `MOONMIND_WORKFLOW_DOCKER_MODE` is an unsupported value, when the service starts, then startup fails with a deterministic configuration error.
- Given mode is `disabled`, when the registry snapshot is built or a DooD tool is invoked directly, then all Docker-backed tools are omitted or denied at runtime.
- Given mode is `profiles`, when the registry snapshot is built, then profile-backed and curated DooD tools are available while unrestricted tools are omitted and denied.
- Given mode is `unrestricted`, when the registry snapshot is built, then profile-backed and unrestricted DooD tools are available while session-side Docker authority remains unchanged.

Requirements
- Normalize the deployment-owned Docker mode from `MOONMIND_WORKFLOW_DOCKER_MODE` at settings load time.
- Keep `disabled`, `profiles`, and `unrestricted` as the only supported modes.
- Make registry exposure mode-aware without relying on registration alone for enforcement.
- Return deterministic denial behavior for mode-forbidden tool invocations.

Relevant Implementation Notes
- Preserve MM-499 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the source design reference for purpose, core decisions, Docker workflow permission modes, and stable design rules.
- Treat `MOONMIND_WORKFLOW_DOCKER_MODE` as deployment-owned configuration normalized at settings-load time.
- Support only the `disabled`, `profiles`, and `unrestricted` workflow Docker modes.
- Ensure registry exposure is mode-aware, but also enforce denials at runtime so registration alone is not the only guardrail.
- In `disabled` mode, omit Docker-backed tools from the registry snapshot and deny direct invocation attempts deterministically.
- In `profiles` mode, expose only curated and profile-backed Docker workload tools while omitting and denying unrestricted tools.
- In `unrestricted` mode, expose profile-backed and unrestricted Docker workload tools without broadening session-side Docker authority.
- Keep startup validation fail-fast and deterministic for unsupported mode values.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-499 is blocked by MM-500, whose embedded status is Backlog.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/ManagedAgents/DockerOutOfDocker.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-499 were found under `specs/`; specification is the first incomplete stage.

## User Story - Govern Workflow Docker Access

**Summary**: As a deployment operator, I want workflow Docker access to follow one explicit deployment mode so MoonMind exposes and enforces only the workload tools allowed for that environment.

**Goal**: Operators can control whether workflow Docker-backed tools are unavailable, profile-limited, or unrestricted through one deployment-owned mode, and the system applies that mode consistently during startup, tool discovery, and runtime enforcement.

**Independent Test**: Start the system in each supported workflow Docker mode plus one unsupported value, then verify the effective mode defaults correctly, invalid mode values fail deterministically at startup, registry discovery reflects the selected mode, runtime invocation denies mode-forbidden tools, and MM-499 traceability is preserved.

**Acceptance Scenarios**:

1. **Given** workflow Docker mode is not explicitly configured, **When** the system loads deployment configuration, **Then** the effective workflow Docker mode is `profiles`.
2. **Given** workflow Docker mode is set to an unsupported value, **When** the system starts, **Then** startup fails with a deterministic configuration error instead of silently choosing another mode.
3. **Given** workflow Docker mode is `disabled`, **When** workflow tool discovery or direct invocation occurs, **Then** Docker-backed workload tools are unavailable and denied.
4. **Given** workflow Docker mode is `profiles`, **When** workflow tool discovery occurs, **Then** only curated and profile-backed Docker workload tools are available while unrestricted Docker workload tools remain unavailable.
5. **Given** workflow Docker mode is `unrestricted`, **When** workflow tool discovery occurs, **Then** profile-backed and unrestricted Docker workload tools are available without altering session-side Docker authority.
6. **Given** a caller attempts to invoke a Docker-backed workflow tool that the selected mode does not allow, **When** the invocation reaches the runtime boundary, **Then** the system returns a deterministic denial outcome.

### Edge Cases

- Startup receives an empty, misspelled, or unexpected workflow Docker mode value.
- Registry discovery and direct invocation disagree about whether a tool is allowed for the selected mode.
- Curated or profile-backed Docker tools are accidentally hidden in `profiles` mode.
- Unrestricted Docker tools leak into `profiles` mode through fallback registration behavior.
- Session-side Docker authority changes as a side effect of enabling unrestricted workflow mode.

## Assumptions

- MM-499 is limited to deployment-owned workflow Docker mode selection and enforcement, not broader redesign of managed-session Docker authority.
- Curated, profile-backed, and unrestricted Docker-backed workload tools are already distinguishable by the system's existing tool metadata.
- The MM-500 blocker relationship is tracked operationally outside this specification and does not change the single-story scope definition for MM-499.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/ManagedAgents/DockerOutOfDocker.md` §1 | Workflow Docker access must be governed by deployment-owned policy rather than implicit runtime behavior. | In scope | FR-001, FR-002 |
| DESIGN-REQ-003 | `docs/ManagedAgents/DockerOutOfDocker.md` §2 | The supported workflow Docker modes are limited to disabled, profiles, and unrestricted. | In scope | FR-001, FR-003 |
| DESIGN-REQ-007 | `docs/ManagedAgents/DockerOutOfDocker.md` §6 | Omitted workflow Docker mode configuration resolves deterministically to profiles. | In scope | FR-002 |
| DESIGN-REQ-008 | `docs/ManagedAgents/DockerOutOfDocker.md` §6 | Unsupported workflow Docker mode values fail deterministically during startup. | In scope | FR-003 |
| DESIGN-REQ-009 | `docs/ManagedAgents/DockerOutOfDocker.md` §6 | Disabled mode omits and denies Docker-backed workflow tools. | In scope | FR-004, FR-007 |
| DESIGN-REQ-010 | `docs/ManagedAgents/DockerOutOfDocker.md` §6 | Profiles mode exposes only curated and profile-backed Docker workflow tools while keeping unrestricted tools unavailable. | In scope | FR-005, FR-007 |
| DESIGN-REQ-011 | `docs/ManagedAgents/DockerOutOfDocker.md` §19 | Unrestricted workflow mode exposes additional workload tools without changing session-side Docker authority. | In scope | FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST govern workflow Docker-backed tool availability through one deployment-owned workflow Docker mode.
- **FR-002**: The system MUST default the effective workflow Docker mode to `profiles` when no explicit mode is configured.
- **FR-003**: The system MUST reject unsupported workflow Docker mode values with a deterministic startup error.
- **FR-004**: The system MUST omit Docker-backed workflow tools from discovery and deny their invocation when workflow Docker mode is `disabled`.
- **FR-005**: The system MUST expose only curated and profile-backed Docker-backed workflow tools when workflow Docker mode is `profiles`.
- **FR-006**: The system MUST expose both profile-backed and unrestricted Docker-backed workflow tools when workflow Docker mode is `unrestricted` without changing session-side Docker authority.
- **FR-007**: The system MUST enforce the selected workflow Docker mode at runtime invocation boundaries rather than relying on discovery or registration behavior alone.
- **FR-008**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-499.

### Key Entities

- **Workflow Docker Mode**: The deployment-owned mode that determines whether Docker-backed workflow tools are disabled, profile-limited, or unrestricted.
- **Docker-Backed Workflow Tool**: A workload tool whose availability depends on workflow Docker access policy.
- **Registry Discovery View**: The workflow-facing tool listing that reflects which Docker-backed tools are available for the selected mode.
- **Deterministic Denial Outcome**: A predictable runtime response returned when a caller invokes a mode-forbidden Docker-backed workflow tool.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation confirms omitted workflow Docker mode configuration resolves to `profiles` every time.
- **SC-002**: Validation confirms unsupported workflow Docker mode values fail startup with one deterministic configuration error path.
- **SC-003**: Validation confirms `disabled` mode removes Docker-backed workflow tools from discovery and denies direct invocation.
- **SC-004**: Validation confirms `profiles` mode exposes curated and profile-backed Docker-backed workflow tools while excluding unrestricted ones.
- **SC-005**: Validation confirms `unrestricted` mode exposes profile-backed and unrestricted Docker-backed workflow tools without broadening session-side Docker authority.
- **SC-006**: Validation confirms runtime invocation enforcement matches discovery behavior for every supported workflow Docker mode.
- **SC-007**: Traceability review confirms MM-499 and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-011 remain preserved in MoonSpec artifacts and downstream implementation evidence.
