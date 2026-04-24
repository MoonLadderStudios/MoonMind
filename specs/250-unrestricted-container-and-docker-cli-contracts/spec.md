# Feature Specification: Unrestricted Container and Docker CLI Contracts

**Feature Branch**: `250-unrestricted-container-and-docker-cli-contracts`  
**Created**: 2026-04-24  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-501 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-501-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched MM-501 under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-501 MoonSpec Orchestration Input

## Source

- Jira issue: MM-501
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add unrestricted container and Docker CLI execution contracts
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-501 from MM project
Summary: Add unrestricted container and Docker CLI execution contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-501 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-501: Add unrestricted container and Docker CLI execution contracts

Source Reference
- Source Document: `docs/ManagedAgents/DockerOutOfDocker.md`
- Source Title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
- Source Sections:
  - 2. Core decisions
  - 7. Supported container roles
  - 11.4 container.run_container
  - 11.5 container.run_docker
  - 18.2-18.4 Example flows
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-010
  - DESIGN-REQ-017
  - DESIGN-REQ-022
  - DESIGN-REQ-025

User Story
As a trusted deployment operator, I can allow arbitrary runtime containers and explicit Docker CLI workloads through separate unrestricted MoonMind tools without weakening the normal profile-backed contract.

Acceptance Criteria
- Given mode is unrestricted, when `container.run_container` is invoked with a runtime-selected image plus workspace paths and declared outputs, then MoonMind launches the container without requiring a pre-registered runner profile.
- Given `container.run_container` includes arbitrary host-path mounts, privileged flags, host networking, or implicit auth inheritance, then validation rejects the request because those capabilities are outside the structured unrestricted contract.
- Given mode is unrestricted, when `container.run_docker` is invoked, then `command[0]` must equal `docker` and the command runs as a Docker CLI invocation rather than a general shell surface.
- Given mode is `disabled` or `profiles`, when unrestricted tools are invoked, then MoonMind returns deterministic denial codes such as `unrestricted_container_disabled` or `unrestricted_docker_disabled`.

Requirements
- Expose `container.run_container` as the first-class unrestricted arbitrary-container contract.
- Expose `container.run_docker` as the explicit Docker CLI escape hatch and not as generic shell access.
- Keep unrestricted execution deployment-gated and auditable.
- Preserve the meaning of `container.run_workload` as profile-backed even when unrestricted mode is enabled.

Relevant Implementation Notes
- Preserve MM-501 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the source design reference for core decisions, supported container roles, unrestricted container execution, Docker CLI execution, and the documented example flows.
- Keep unrestricted execution deployment-gated and auditable rather than widening it into unbounded shell access.
- Treat `container.run_container` and `container.run_docker` as distinct unrestricted tool contracts with explicit validation boundaries.
- Ensure `container.run_docker` remains a Docker CLI-specific surface where `command[0]` must be `docker`, not a general-purpose shell mechanism.
- Preserve the profile-backed meaning of `container.run_workload` even when unrestricted mode is enabled.
- Reject unrestricted requests that attempt arbitrary host-path mounts, privileged flags, host networking, or implicit auth inheritance outside the structured contract.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-501 blocks MM-500, whose embedded status is In Progress.
- Trusted Jira link metadata at fetch time shows MM-501 is blocked by MM-502, whose embedded status is Backlog.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/ManagedAgents/DockerOutOfDocker.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-501 were found under `specs/`; specification is the first incomplete stage.

## User Story - Run Unrestricted Containers And Docker CLI Workloads

**Summary**: As a trusted deployment operator, I want separate unrestricted container and Docker CLI workflow tools so MoonMind can support operator-approved unrestricted execution without weakening the normal profile-backed workload contract.

**Goal**: Deployment operators can enable a narrowly defined unrestricted execution mode where MoonMind exposes `container.run_container` and `container.run_docker`, enforces their distinct validation boundaries, keeps `container.run_workload` profile-backed, and returns deterministic denials when unrestricted execution is not allowed.

**Independent Test**: In each workflow Docker mode, invoke `container.run_container`, `container.run_docker`, and `container.run_workload` with allowed and disallowed inputs, then verify unrestricted mode permits only the defined unrestricted contracts, `container.run_docker` requires a Docker CLI invocation, structured validation rejects forbidden host-path, privilege, networking, or auth inheritance inputs, non-unrestricted modes return deterministic denial outcomes, and MM-501 traceability remains preserved.

**Acceptance Scenarios**:

1. **Given** workflow Docker mode is `unrestricted`, **When** `container.run_container` is invoked with a runtime-selected image, workspace paths, and declared outputs that fit the unrestricted contract, **Then** MoonMind launches the container without requiring a pre-registered runner profile.
2. **Given** `container.run_container` includes arbitrary host-path mounts, privileged flags, host networking, or implicit auth inheritance, **When** the request is validated, **Then** MoonMind rejects the request because those capabilities are outside the structured unrestricted contract.
3. **Given** workflow Docker mode is `unrestricted`, **When** `container.run_docker` is invoked, **Then** MoonMind executes it only as a Docker CLI workload where `command[0]` is `docker` rather than as a general shell surface.
4. **Given** workflow Docker mode is `disabled` or `profiles`, **When** unrestricted tools are invoked, **Then** MoonMind returns deterministic denial outcomes such as `unrestricted_container_disabled` or `unrestricted_docker_disabled`.
5. **Given** unrestricted execution mode is enabled, **When** `container.run_workload` is invoked, **Then** MoonMind preserves its existing profile-backed meaning instead of treating it as an unrestricted alias.

### Edge Cases

- A caller attempts to pass arbitrary host-path mounts, privileged execution flags, or host networking through `container.run_container`.
- A caller uses `container.run_docker` with a command whose first token is not `docker`.
- Registry discovery exposes unrestricted tools in a mode that runtime invocation later denies, or vice versa.
- Enabling unrestricted mode accidentally widens `container.run_workload` into raw image or shell-oriented execution.
- Unrestricted example flows diverge from the contract enforced at the worker boundary.

## Assumptions

- MM-501 is scoped to the unrestricted workflow execution contracts and their boundaries, not to redesigning profile-backed workload semantics already covered by adjacent stories.
- The blocker relationship to MM-502 is tracked operationally outside this specification and does not change the single-story runtime scope captured here.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | `docs/ManagedAgents/DockerOutOfDocker.md` §2 | Workflow Docker execution modes must preserve explicit boundaries between profile-backed and unrestricted behavior. | In scope | FR-001, FR-004, FR-005 |
| DESIGN-REQ-010 | `docs/ManagedAgents/DockerOutOfDocker.md` §2 | Unrestricted workflow mode exposes additional workload capabilities without weakening unrelated execution boundaries. | In scope | FR-001, FR-004, FR-005 |
| DESIGN-REQ-017 | `docs/ManagedAgents/DockerOutOfDocker.md` §11.4-11.5 | The user-facing unrestricted tool surface must distinguish arbitrary-container execution from explicit Docker CLI execution. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-022 | `docs/ManagedAgents/DockerOutOfDocker.md` §18.2-18.4 | Example unrestricted execution flows must remain consistent with the formal unrestricted tool contracts and validation behavior. | In scope | FR-002, FR-003, FR-006 |
| DESIGN-REQ-025 | `docs/ManagedAgents/DockerOutOfDocker.md` §7 | Profile-backed workload tools remain part of the supported container-role model and must not silently change meaning when unrestricted mode is enabled. | In scope | FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose `container.run_container` as the first-class unrestricted arbitrary-container workflow contract for runtime-selected container execution.
- **FR-002**: The system MUST reject `container.run_container` requests that include arbitrary host-path mounts, privileged execution flags, host networking, or implicit auth inheritance outside the structured unrestricted contract.
- **FR-003**: The system MUST expose `container.run_docker` as a distinct unrestricted Docker CLI workload contract and MUST require `command[0]` to equal `docker`.
- **FR-004**: The system MUST deterministically deny unrestricted tool invocations when workflow Docker mode is `disabled` or `profiles`.
- **FR-005**: The system MUST preserve the profile-backed meaning of `container.run_workload` even when unrestricted mode is enabled.
- **FR-006**: The system MUST keep unrestricted example flows and operator-visible behavior aligned with the same validation boundaries enforced at runtime.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-501.

### Key Entities

- **Unrestricted Container Request**: The structured request for `container.run_container` that permits runtime-selected container execution within bounded unrestricted validation rules.
- **Docker CLI Workload Request**: The structured request for `container.run_docker` that allows Docker CLI execution only when the command is explicitly a Docker invocation.
- **Profile-Backed Workload Contract**: The existing `container.run_workload` contract whose meaning remains tied to approved runner profiles even when unrestricted mode is enabled.
- **Deterministic Denial Outcome**: The predictable non-success result returned when unrestricted execution is invoked from a non-unrestricted workflow Docker mode or with forbidden unrestricted inputs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves `container.run_container` accepts only the structured unrestricted request shape and rejects forbidden mounts, privilege, networking, or auth inheritance inputs.
- **SC-002**: Validation proves `container.run_docker` executes only Docker CLI invocations where `command[0]` is `docker`.
- **SC-003**: Validation proves `disabled` and `profiles` modes return deterministic denials for unrestricted tool invocations.
- **SC-004**: Validation proves `container.run_workload` remains profile-backed and does not become an unrestricted alias when unrestricted mode is enabled.
- **SC-005**: Validation proves operator-visible unrestricted example flows and runtime validation behavior remain aligned.
- **SC-006**: Traceability review confirms MM-501 and DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-022, and DESIGN-REQ-025 remain preserved in MoonSpec artifacts and downstream verification evidence.
