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
