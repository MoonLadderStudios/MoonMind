# Feature Specification: Profile-Backed Workload Contracts

**Feature Branch**: `249-profile-backed-workload-contracts`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-500 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-500-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched MM-500 under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-500 MoonSpec Orchestration Input

## Source

- Jira issue: MM-500
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Implement profile-backed workload and helper tool contracts
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-500 from MM project
Summary: Implement profile-backed workload and helper tool contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-500 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-500: Implement profile-backed workload and helper tool contracts

Source Reference
- Source document: `docs/ManagedAgents/DockerOutOfDocker.md`
- Source title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
- Source sections:
  - 7. Supported container roles
  - 11. User-facing tool surface
  - 12. Runner profile model
  - 18.1 Unreal test run in profiles mode
- Coverage IDs:
  - DESIGN-REQ-012
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-025

User Story
As a workflow author, I can run curated and profile-backed workload containers and helpers through stable MoonMind tool contracts that validate against runner profiles instead of raw container inputs.

Acceptance Criteria
- Given mode is profiles or unrestricted, when `container.run_workload` is invoked with an approved `profileId`, then MoonMind resolves the runner profile and launches the workload with profile-defined mounts, env policy, resources, timeout, and cleanup.
- Given `container.run_workload` is invoked with raw image strings, arbitrary host-path mounts, or unrestricted privilege fields, then validation fails because the contract remains profile-backed.
- Given `container.start_helper` and `container.stop_helper` are invoked with an approved helper profile, then MoonMind treats the helper as an explicitly owned, bounded workload lifecycle rather than an arbitrary detached service.
- Given mode is disabled, when any profile-backed workload or helper tool is invoked, then the request is denied deterministically.

Requirements
- Keep `container.run_workload` generic but strictly profile-validated.
- Preserve bounded helper semantics for `container.start_helper` and `container.stop_helper`.
- Model normal execution around runner profiles rather than unrestricted image strings.
- Keep curated domain tools such as `unreal.run_tests` aligned with the same DooD execution model.

Relevant Implementation Notes
- Preserve MM-500 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the source design reference for supported container roles, the user-facing tool surface, runner profile modeling, and Unreal test execution in profiles mode.
- Keep workload and helper execution contracts profile-backed; do not widen them to arbitrary image, mount, or privilege inputs.
- Ensure helper lifecycle semantics remain explicitly owned and bounded rather than detached-service style.
- Keep curated domain tools aligned with the same runner-profile and DooD execution model as the generic container tools.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-500 blocks MM-499, whose embedded status is In Progress.
- Trusted Jira link metadata at fetch time shows MM-500 is blocked by MM-501, whose embedded status is Backlog.

Needs Clarification
- None
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/ManagedAgents/DockerOutOfDocker.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-500 were found under `specs/`; specification is the first incomplete stage.

## User Story - Run Profile-Backed Workloads

**Summary**: As a workflow author, I want Docker-backed workload and helper tools to stay profile-backed so MoonMind launches only approved container shapes instead of arbitrary raw container requests.

**Goal**: Workflow authors can launch approved one-shot workloads and bounded helpers through stable MoonMind tool contracts that enforce runner-profile policy, preserve deterministic helper lifecycle ownership, and deny disabled-mode execution.

**Independent Test**: Through the worker-facing tool dispatcher, invoke `container.run_workload`, `container.start_helper`, and `container.stop_helper` with approved profiles plus invalid raw-container fields and disabled-mode execution, then verify approved profile-backed requests resolve through the runner profile registry, helpers remain bounded-service lifecycles, invalid raw fields are rejected, disabled mode denies execution, curated tools remain profile-backed, and MM-500 traceability is preserved.

**Acceptance Scenarios**:

1. **Given** workflow Docker mode is `profiles` or `unrestricted`, **When** `container.run_workload` is invoked with an approved `profileId`, **Then** MoonMind resolves that runner profile and launches the workload with profile-defined mounts, env policy, resources, timeout, and cleanup.
2. **Given** `container.run_workload` receives raw image strings, arbitrary host-path mounts, or unrestricted privilege fields, **When** the request is validated, **Then** MoonMind rejects the request instead of widening the profile-backed contract.
3. **Given** `container.start_helper` and `container.stop_helper` are invoked with an approved helper profile, **When** the helper lifecycle runs, **Then** MoonMind treats the helper as an explicitly owned bounded-service workload with readiness and teardown metadata rather than as an arbitrary detached service.
4. **Given** workflow Docker mode is `disabled`, **When** a profile-backed workload or helper tool is invoked, **Then** MoonMind returns a deterministic denial outcome instead of launching the request.
5. **Given** curated tools such as `unreal.run_tests` or `moonmind.integration_ci` use the Docker-backed workload path, **When** they are invoked, **Then** MoonMind continues to resolve them through approved runner profiles instead of raw container inputs.

### Edge Cases

- A workflow tries to pass raw `image`, `mounts`, `devices`, or `privileged` fields through `container.run_workload`.
- A helper profile uses bounded-service readiness or teardown metadata that must remain distinct from one-shot workload results.
- Disabled-mode registration and dispatcher execution disagree about whether profile-backed tools are allowed.
- Curated tools drift away from the same runner-profile model used by `container.run_workload`.

## Assumptions

- MM-500 is scoped to the existing profile-backed workload and helper contract path rather than the unrestricted Docker mode introduced by adjacent stories.
- The blocker relationship to MM-501 is tracked operationally outside this specification and does not change the one-story scope or runtime requirements captured here.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-012 | `docs/ManagedAgents/DockerOutOfDocker.md` §7 | Supported workload-container roles must stay on the Docker workload plane rather than becoming arbitrary session-owned containers. | In scope | FR-001, FR-004 |
| DESIGN-REQ-017 | `docs/ManagedAgents/DockerOutOfDocker.md` §11 | The user-facing workload tool surface must remain profile-backed for `container.run_workload`, `container.start_helper`, and `container.stop_helper`. | In scope | FR-001, FR-003, FR-004 |
| DESIGN-REQ-018 | `docs/ManagedAgents/DockerOutOfDocker.md` §12 | Runner profiles define approved mounts, env policy, resources, timeout, and cleanup for normal workload execution. | In scope | FR-002, FR-004 |
| DESIGN-REQ-025 | `docs/ManagedAgents/DockerOutOfDocker.md` §18.1 | Curated domain workload tools such as Unreal test execution must stay aligned with the same profiles-mode execution model. | In scope | FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose `container.run_workload` as a profile-backed one-shot Docker workload tool that requires an approved `profileId`.
- **FR-002**: The system MUST resolve approved profile-backed workload and helper requests through the runner profile registry and apply profile-defined mounts, env policy, resources, timeout, and cleanup semantics.
- **FR-003**: The system MUST reject raw image strings, arbitrary host-path mounts, unrestricted device fields, and unrestricted privilege fields from the `container.run_workload` contract.
- **FR-004**: The system MUST expose `container.start_helper` and `container.stop_helper` as approved helper-profile tools with bounded-service lifecycle, readiness, and teardown behavior.
- **FR-005**: The system MUST keep curated workload tools such as `unreal.run_tests` and `moonmind.integration_ci` aligned with the same runner-profile-backed execution model as `container.run_workload`.
- **FR-006**: The system MUST deterministically deny profile-backed workload and helper tool execution when workflow Docker mode is `disabled`.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-500.

### Key Entities

- **Runner Profile**: The curated workload-container definition that supplies approved image, mounts, env policy, resources, timeout, cleanup, and helper lifecycle rules.
- **Profile-Backed Workload Request**: The validated request for `container.run_workload`, `container.start_helper`, or `container.stop_helper` that must resolve through a runner profile rather than raw container input.
- **Bounded Helper Lifecycle**: The explicitly owned helper lifecycle that produces readiness and teardown metadata instead of a detached-service contract.
- **Deterministic Denial Outcome**: The non-retryable policy response returned when disabled mode forbids profile-backed workload execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves approved `container.run_workload` requests resolve through runner profiles and carry profile-backed metadata rather than raw container-shape input.
- **SC-002**: Validation proves raw container fields on `container.run_workload` are rejected before launch.
- **SC-003**: Validation proves helper start and stop requests preserve bounded-service readiness and teardown behavior.
- **SC-004**: Validation proves disabled mode denies profile-backed workload execution deterministically.
- **SC-005**: Validation proves curated workload tools remain aligned with the same runner-profile-backed model.
- **SC-006**: Traceability review confirms MM-500 and DESIGN-REQ-012, DESIGN-REQ-017, DESIGN-REQ-018, and DESIGN-REQ-025 remain preserved in MoonSpec artifacts and downstream verification evidence.
