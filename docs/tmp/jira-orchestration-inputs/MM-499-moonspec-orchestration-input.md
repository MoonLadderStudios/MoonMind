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
