# MM-503 MoonSpec Orchestration Input

## Source

- Jira issue: MM-503
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Route DooD tools through the shared docker_workload execution plane
- Labels: `moonmind-workflow-mm-f5953598-583e-468e-b58f-219d2fe54fc3`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-503 from MM project
Summary: Route DooD tools through the shared docker_workload execution plane
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-503 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-503: Route DooD tools through the shared docker_workload execution plane

Source Reference
- Source document: `docs/ManagedAgents/DockerOutOfDocker.md`
- Source title: DockerOutOfDocker: Docker-backed Specialized Workload Containers for MoonMind
- Source sections:
  - 5.4 Logical execution capability
  - 8. Ownership and routing model
  - 13. Execution model and launch semantics
  - 16. Timeout, cancellation, and cleanup
  - 17. Compose and runtime shape
- Coverage IDs:
  - DESIGN-REQ-006
  - DESIGN-REQ-019
  - DESIGN-REQ-020
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a workflow runtime owner, I can execute all DooD-backed tools through one trusted `docker_workload` capability and a shared launcher pipeline that applies labels, timeouts, cancellation, and cleanup consistently.

Acceptance Criteria
- Given any DooD-backed tool, when it is executed, then the request routes through `mm.tool.execute` with required capability `docker_workload`.
- Given a workload starts, then MoonMind applies deterministic labels including `task_run_id`, `step_id`, `attempt`, `tool_name`, `docker_mode`, and workload access class.
- Given timeout or cancellation occurs, then MoonMind attempts graceful stop, escalates to kill after the grace period when needed, captures remaining diagnostics where available, and records bounded terminal metadata.
- Given structured containers are created through `container.run_workload`, `container.start_helper`, or `container.run_container`, then MoonMind owns their cleanup; given arbitrary resources are created by `container.run_docker`, then MoonMind only performs cleanup when ownership can be reliably identified.

Requirements
- Keep `docker_workload` as the stable logical capability independent of the current physical fleet assignment.
- Share one launcher pipeline across profile-backed, unrestricted container, and unrestricted Docker CLI launch classes.
- Apply explicit helper lifecycle ownership instead of relying on long-running container side effects.
- Allow a future dedicated Docker workload fleet without changing the contract exposed to workflows.

Relevant Implementation Notes
- Preserve MM-503 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/ManagedAgents/DockerOutOfDocker.md` as the source design reference for logical execution capability, ownership and routing, launch semantics, timeout/cancellation/cleanup behavior, and compose/runtime shape.
- Keep all DooD-backed tools on one trusted `docker_workload` execution path rather than splitting execution semantics across multiple capability classes.
- Ensure deterministic label application, bounded cancellation handling, and cleanup ownership remain part of the shared launcher behavior.
- Limit cleanup for arbitrary Docker-created resources to cases where MoonMind can reliably determine ownership.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-503 blocks MM-502, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-503 is blocked by MM-504, whose embedded status is Backlog.

Needs Clarification
- None
