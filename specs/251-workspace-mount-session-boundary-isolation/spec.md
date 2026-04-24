# Feature Specification: Workspace, Mount, and Session-Boundary Isolation

**Feature Branch**: `251-workspace-mount-session-boundary-isolation`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-502 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-502-moonspec-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched MM-502 under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-502 MoonSpec Orchestration Input

## Source

- Jira issue: MM-502
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce workspace, mount, and session-boundary isolation
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-502`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-502 from MM project
Summary: Enforce workspace, mount, and session-boundary isolation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-502 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-502: Enforce workspace, mount, and session-boundary isolation

Source Reference
Source Document: docs/ManagedAgents/DockerOutOfDocker.md
Source Title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
Source Sections:
- 4. Terminology
- 8. Ownership and routing model
- 9. Session-plane interaction model
- 10. Workspace and volume contract
- 15.2-15.5 Security and policy controls
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-022
As a platform maintainer, I can guarantee that workload containers operate only on MoonMind-owned task paths and remain isolated from managed-session identity and provider auth state unless policy explicitly allows otherwise.

## Normalized Jira Detail

Acceptance criteria:
- Given a DooD request, when repoDir, artifactsDir, scratchDir, or declared outputs resolve outside the workspace root, then the request is rejected before launch.
- Given a managed Codex session requests a DooD tool, when the workload runs, then the session may receive results and association metadata but does not gain raw Docker socket or unrestricted DOCKER_HOST access.
- Given a workload is launched from a session-assisted step, then any session_id or source_turn_id fields are treated as association metadata only and do not convert the workload container into a managed session.
- Given a workload request lacks an explicit credential policy, then provider auth volumes are not mounted into the workload container by default.

Requirements:
- Preserve the architectural separation between session containers and workload containers.
- Constrain structured DooD contracts to MoonMind-owned workspace paths and approved caches.
- Prevent automatic auth-volume inheritance into workload containers.
- Keep generic shell access and session-side Docker bypasses out of scope.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/ManagedAgents/DockerOutOfDocker.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-502 were found under `specs/`; specification is the first incomplete stage.

## User Story - Enforce Workload Isolation Boundaries

**Summary**: As a platform maintainer, I want MoonMind workload launches to stay inside MoonMind-owned task paths and remain isolated from managed-session identity and provider authentication state so Docker-backed workloads cannot silently widen session authority.

**Goal**: MoonMind can launch Docker-backed workloads for a task while enforcing workspace ownership, bounded mount and output rules, session/workload identity separation, and explicit policy control over any credential sharing.

**Independent Test**: Submit Docker-backed workload requests from direct tool calls and session-assisted steps, then verify valid requests remain confined to MoonMind-owned task paths, invalid paths are rejected before launch, session-associated launches do not change workload identity or grant raw Docker authority to the session, and auth volumes are absent unless an explicit credential policy allows them.

**Acceptance Scenarios**:

1. **Given** a Docker-backed workload request whose repo, artifact, scratch, or declared output paths resolve outside the MoonMind-owned task workspace, **When** MoonMind validates the request, **Then** it rejects the request before any workload launch occurs.
2. **Given** a managed Codex session requests a Docker-backed workload through a MoonMind tool, **When** MoonMind launches that workload, **Then** the session receives only the returned result and association metadata, and does not gain raw Docker socket access or unrestricted Docker host authority.
3. **Given** a workload is launched from a session-assisted step, **When** MoonMind records session association metadata such as session or turn identifiers, **Then** that metadata is treated only as association context and does not convert the workload into a managed session.
4. **Given** a workload request does not declare an explicit credential-sharing policy, **When** MoonMind prepares workload mounts, **Then** provider authentication volumes are not mounted into the workload container by default.
5. **Given** a Docker-backed workload request stays within MoonMind-owned task paths and follows the approved mount and output rules, **When** MoonMind validates the request, **Then** it may proceed without weakening the separation between the session plane and the workload plane.

### Edge Cases

- A caller provides a path that appears relative or normalized but resolves outside the shared workspace root after canonical resolution.
- A session-assisted workload launch includes session identifiers and is incorrectly interpreted as permission to inherit managed-session authority.
- A workload attempts to declare outputs outside the artifacts directory while otherwise using valid workspace paths.
- A credential-related mount is omitted from the request but would be inherited implicitly by default behavior.
- Tool routing and runtime enforcement disagree about whether a session-assisted workload is allowed to use Docker-backed execution.

## Assumptions

- MM-502 is limited to enforcing runtime isolation and workspace-boundary behavior for Docker-backed workload launches, not to designing new unrestricted container capabilities already covered by adjacent stories.
- Explicit credential-sharing policy, when allowed by deployment rules, is defined elsewhere; this story only requires safe default isolation when no such policy is present.
- The blocker relationship to MM-503 is tracked operationally outside this specification and does not change the single-story runtime scope captured here.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-002 | `docs/ManagedAgents/DockerOutOfDocker.md` §8-9 | Docker-backed workloads are control-plane-launched workload executions that remain distinct from managed session identity and authority. | In scope | FR-002, FR-003, FR-006 |
| DESIGN-REQ-004 | `docs/ManagedAgents/DockerOutOfDocker.md` §4, §8, §10 | Profile-backed and session-assisted workload launches must preserve the architectural separation between session containers and workload containers. | In scope | FR-002, FR-003, FR-006 |
| DESIGN-REQ-005 | `docs/ManagedAgents/DockerOutOfDocker.md` §10 | Structured Docker-backed workload contracts must operate only on MoonMind-owned workspace paths and declared output locations. | In scope | FR-001, FR-004 |
| DESIGN-REQ-013 | `docs/ManagedAgents/DockerOutOfDocker.md` §9.2, §15.2 | Managed sessions must not receive raw Docker socket access or unrestricted Docker host authority merely because they request a workload through MoonMind. | In scope | FR-002, FR-006 |
| DESIGN-REQ-014 | `docs/ManagedAgents/DockerOutOfDocker.md` §10.2-10.5, §15.4 | Path validation and mount policy must reject workspace, artifact, scratch, and output paths that escape MoonMind-owned task roots. | In scope | FR-001, FR-004 |
| DESIGN-REQ-015 | `docs/ManagedAgents/DockerOutOfDocker.md` §9.4 | Session and turn identifiers attached to workload launches are association metadata only and must not alter workload identity. | In scope | FR-003 |
| DESIGN-REQ-016 | `docs/ManagedAgents/DockerOutOfDocker.md` §10.4, §15.6 | Provider authentication material must not flow into workload containers by default; credential sharing requires explicit policy. | In scope | FR-005 |
| DESIGN-REQ-022 | `docs/ManagedAgents/DockerOutOfDocker.md` §15.2-15.5 | Security and policy controls for Docker-backed workloads must remain consistent with ownership, mount, and isolation rules. | In scope | FR-001, FR-002, FR-004, FR-005, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST reject any Docker-backed workload request whose repository, artifact, scratch, or declared output path resolves outside the MoonMind-owned task workspace before launch begins.
- **FR-002**: The system MUST preserve the separation between managed session authority and Docker-backed workload execution so that requesting a workload through MoonMind does not grant the managed session raw Docker socket access or unrestricted Docker host authority.
- **FR-003**: The system MUST treat session or turn identifiers attached to a Docker-backed workload launch as association metadata only and MUST NOT use them to redefine the workload as a managed session.
- **FR-004**: The system MUST constrain workload mounts and declared outputs to the approved MoonMind-owned workspace and artifact roots enforced by the workload contract.
- **FR-005**: The system MUST omit provider authentication volumes from workload mounts by default when a workload request does not carry an explicit credential-sharing policy.
- **FR-006**: The system MUST keep tool routing, validation, and runtime enforcement aligned so session-assisted Docker-backed workloads follow the same ownership, isolation, and policy boundaries as other workload launches.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-502.

### Key Entities

- **MoonMind-Owned Task Workspace**: The bounded task workspace roots under which repository, artifact, scratch, and declared output paths must resolve for Docker-backed workloads.
- **Docker-Backed Workload Request**: The structured workload launch request that includes workspace-related paths, declared outputs, mount intent, and optional association metadata.
- **Session Association Metadata**: Session or turn identifiers attached to a workload launch only to preserve traceability between a managed session step and the launched workload.
- **Explicit Credential-Sharing Policy**: The deployment-approved policy signal that allows credential material to be mounted into a workload; absence of this signal preserves default isolation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves Docker-backed workload requests that resolve any repo, artifact, scratch, or output path outside the MoonMind-owned task workspace are rejected before launch.
- **SC-002**: Validation proves managed session-initiated workloads do not provide the session raw Docker socket access or unrestricted Docker host authority.
- **SC-003**: Validation proves session or turn identifiers associated with a workload do not change workload identity into a managed session.
- **SC-004**: Validation proves workload mounts and declared outputs remain confined to approved workspace and artifact roots.
- **SC-005**: Validation proves provider authentication volumes are absent from workload mounts unless an explicit credential-sharing policy is present.
- **SC-006**: Traceability review confirms MM-502 and DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, and DESIGN-REQ-022 remain preserved in MoonSpec artifacts and downstream verification evidence.
