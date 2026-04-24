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

Relevant implementation notes:
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the design source for workspace ownership, routing, session-plane boundaries, workspace/volume contracts, and security/policy controls.
- Preserve the architectural separation between managed sessions and workload containers even when session-scoped metadata is present.
- Keep provider auth volume mounting opt-in through explicit policy rather than implicit inheritance.
- Preserve MM-502 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies:
- Trusted Jira link metadata at fetch time shows MM-502 blocks MM-501, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-502 is blocked by MM-503, whose embedded status is Backlog.

Recommended step instructions:

Complete Jira issue MM-502: Enforce workspace, mount, and session-boundary isolation

Description
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

Acceptance criteria
- Given a DooD request, when repoDir, artifactsDir, scratchDir, or declared outputs resolve outside the workspace root, then the request is rejected before launch.
- Given a managed Codex session requests a DooD tool, when the workload runs, then the session may receive results and association metadata but does not gain raw Docker socket or unrestricted DOCKER_HOST access.
- Given a workload is launched from a session-assisted step, then any session_id or source_turn_id fields are treated as association metadata only and do not convert the workload container into a managed session.
- Given a workload request lacks an explicit credential policy, then provider auth volumes are not mounted into the workload container by default.
Requirements
- Preserve the architectural separation between session containers and workload containers.
- Constrain structured DooD contracts to MoonMind-owned workspace paths and approved caches.
- Prevent automatic auth-volume inheritance into workload containers.
- Keep generic shell access and session-side Docker bypasses out of scope.
