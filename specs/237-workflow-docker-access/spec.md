# Feature Specification: Workflow Docker Access Setting

**Feature Branch**: `237-workflow-docker-access`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**: User description: "Use the Jira preset brief for MM-476 as the canonical Moon Spec orchestration input.

Source: `docs/tmp/jira-orchestration-inputs/MM-476-moonspec-orchestration-input.md`

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
- None"

## User Story - Gate Workflow Docker Workloads

**Summary**: As a MoonMind operator, I want a single runtime setting to enable or deny workflow Docker-backed tool execution so that integration-test and workload access stays explicit, auditable, and limited to the existing DooD boundary.

**Goal**: Workflows can run approved Docker-backed tools when enabled, and receive a deterministic policy denial before any Docker launch when disabled.

**Independent Test**: Configure `MOONMIND_WORKFLOW_DOCKER_ENABLED` to both `true` and `false`, invoke an approved Docker-backed workflow tool, and verify enabled requests reach the workload launcher while disabled requests fail before launcher invocation with `docker_workflows_disabled`.

**Acceptance Scenarios**:

1. **Given** no override is configured, **When** settings load, **Then** workflow Docker access is enabled by default.
2. **Given** `MOONMIND_WORKFLOW_DOCKER_ENABLED=true`, **When** a workflow invokes an approved Docker-backed tool, **Then** MoonMind validates the approved runner profile and may route the request to the Docker-capable workload launcher.
3. **Given** `MOONMIND_WORKFLOW_DOCKER_ENABLED=false`, **When** a workflow invokes any Docker-backed workload tool, **Then** MoonMind fails fast with a policy-denied error containing `docker_workflows_disabled` before validating or launching a container.
4. **Given** a workflow invokes the curated integration-CI tool, **When** Docker workflow access is enabled, **Then** MoonMind maps the request to `./tools/test_integration.sh` in a Docker-visible workspace and returns normal workload result artifact references and bounded diagnostics, including failure diagnostics such as compose-log context when the runner emits them.
5. **Given** a normal agent/session container is launched, **When** workflow Docker access is enabled, **Then** the setting does not grant raw `/var/run/docker.sock` access to that agent/session container.

### Edge Cases

- The setting is disabled while a request uses the generic `container.run_workload` tool.
- The setting is disabled while a request uses a curated Docker-backed tool such as `unreal.run_tests` or `moonmind.integration_ci`.
- The setting is disabled while a direct `workload.run` activity payload reaches the agent-runtime fleet.
- The setting is enabled but the runner profile rejects an unsafe request; existing runner-profile policy remains authoritative.

## Assumptions

- The setting controls workflow-requested Docker workloads only; existing OAuth and managed-session Docker lifecycle operations remain governed by their own runtime controls.
- The curated integration-CI tool may use the existing Docker workload result contract rather than a new persistence model.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST expose `MOONMIND_WORKFLOW_DOCKER_ENABLED` as an app-level workflow setting.
- **FR-002**: The workflow Docker setting MUST default to enabled when no override is configured.
- **FR-003**: When the setting is enabled, approved Docker-backed workflow tools MUST continue to route through the existing DooD worker/tool boundary and runner-profile validation.
- **FR-004**: When the setting is disabled, Docker-backed workflow tools MUST fail before workload registry validation or Docker launcher invocation.
- **FR-005**: Disabled Docker-backed workflow requests MUST return a deterministic policy-denied error that includes `docker_workflows_disabled`.
- **FR-006**: The setting MUST gate DooD capability routing and workflow workload activities, not direct socket mounts.
- **FR-007**: Normal agent/session containers MUST NOT receive raw `/var/run/docker.sock` access because this setting is enabled.
- **FR-008**: MoonMind MUST expose a curated integration-CI Docker-backed tool that runs `./tools/test_integration.sh` from a Docker-visible workspace.
- **FR-009**: The integration-CI tool MUST return artifact-backed stdout, stderr, diagnostics, output refs, workload metadata, and compact status summary through the existing workload result contract; failure diagnostics MUST be able to carry compose-log context when the integration runner emits it.
- **FR-010**: Existing human and GitHub Actions usage of `./tools/test_integration.sh` MUST remain unchanged.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-476`.

### Key Entities

- **Workflow Docker Access Setting**: Runtime configuration value that decides whether workflow-requested Docker-backed tools may proceed.
- **Docker-Backed Workflow Tool**: An approved executable tool or activity that requires the Docker workload plane, including generic DooD tools and curated wrappers.
- **Integration-CI Tool Result**: Existing workload result metadata carrying status, artifact refs, output refs, and bounded diagnostics, including failure context emitted by the integration runner.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove the setting defaults to enabled and honors `MOONMIND_WORKFLOW_DOCKER_ENABLED=false`.
- **SC-002**: Unit tests prove disabled generic and curated Docker-backed tool requests fail with `docker_workflows_disabled` without invoking the launcher.
- **SC-003**: Integration boundary tests prove enabled Docker-backed workflow requests still route to the agent-runtime workload boundary.
- **SC-004**: Integration boundary tests prove the curated integration-CI tool maps to `./tools/test_integration.sh` and returns workload artifact refs through the existing result contract.
- **SC-005**: Tests or code inspection evidence confirms the setting does not add raw Docker socket mounts to normal agent/session containers.
- **SC-006**: Final verification preserves `MM-476` in the active MoonSpec artifacts and delivery metadata.
