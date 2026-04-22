# MM-476 MoonSpec Orchestration Input

## Source

- Jira issue: MM-476
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Workflow Docker Access Setting for Integration Tests
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-476 from MM project
Summary: Workflow Docker Access Setting for Integration Tests
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-476 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-476: Workflow Docker Access Setting for Integration Tests

Task
- Add one app-level setting that controls whether MoonMind workflows may use Docker-backed execution through the existing DooD system.
- Setting name: `MOONMIND_WORKFLOW_DOCKER_ENABLED`
- Default: `true`

Goal
- Allow workflows to run Docker-backed integration tests, such as `./tools/test_integration.sh`, while keeping Docker access behind MoonMind's DooD worker/tool boundary.
- Normal agent/session containers should not receive raw `/var/run/docker.sock`.

Required Behavior
- When `MOONMIND_WORKFLOW_DOCKER_ENABLED=true`, workflows may invoke approved Docker-backed tools.
- When `MOONMIND_WORKFLOW_DOCKER_ENABLED=false`, workflows that require Docker fail fast with a clear policy-denied error.
- The setting gates DooD capability routing, not direct socket mounts.
- Docker access remains limited to the Docker-capable worker/proxy infrastructure.
- Add or expose a curated integration-test tool/activity, for example `moonmind.integration_ci`, that runs `./tools/test_integration.sh`.
- The integration-test runner must execute from a Docker-visible workspace.
- Results must be artifact-backed: stdout, stderr, diagnostics, compose logs on failure, and a compact summary.

Relevant Implementation Notes
- Add `MOONMIND_WORKFLOW_DOCKER_ENABLED` to settings with default `true`.
- Check this setting before routing any workflow-requested Docker workload.
- If disabled, return a deterministic error such as `docker_workflows_disabled`.
- Add the integration-test verifier as a curated DooD-backed tool/activity.
- Ensure cleanup always runs: `docker compose down --remove-orphans`.
- Preserve existing human/GitHub Actions use of `./tools/test_integration.sh`.
- Preserve MM-476 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Validation
- Verify the setting defaults to enabled.
- Verify Docker-backed workflow requests succeed when enabled.
- Verify Docker-backed workflow requests are denied when disabled.
- Verify denial does not start Docker or create workload containers.
- Verify normal agent/session containers are not given raw Docker socket access.
- Verify the integration verifier returns artifact refs and bounded diagnostics.

Non-Goals
- Granting raw Docker socket access to normal agent/session containers.
- Replacing existing human or GitHub Actions usage of `./tools/test_integration.sh`.
- Allowing arbitrary Docker execution outside the approved DooD worker/tool boundary.

Needs Clarification
- None
