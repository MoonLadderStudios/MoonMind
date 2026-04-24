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
