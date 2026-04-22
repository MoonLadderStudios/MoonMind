# Data Model: Workflow Docker Access Setting

## Workflow Docker Access Setting

- **Name**: `workflow_docker_enabled`
- **External env**: `MOONMIND_WORKFLOW_DOCKER_ENABLED`
- **Default**: `true`
- **Owner**: `WorkflowSettings`
- **Behavior**:
  - `true`: approved Docker-backed workflow tools may continue to runner-profile validation and launcher routing.
  - `false`: Docker-backed workflow tools fail before registry validation or launcher invocation with `docker_workflows_disabled`.

## Docker-Backed Workflow Tool

- **Examples**: `container.run_workload`, `container.start_helper`, `container.stop_helper`, `unreal.run_tests`, `moonmind.integration_ci`, direct `workload.run` activity payloads.
- **Validation rule**: All requests must pass the workflow Docker access setting before runner-profile validation or Docker launch.
- **Result model**: Existing workload tool result with `workloadResult`, `requestId`, `profileId`, `workloadStatus`, `stdoutRef`, `stderrRef`, `diagnosticsRef`, `outputRefs`, and `workloadMetadata`. Failure context such as compose-log diagnostics is carried through diagnostics or output refs when emitted by the runner.

## Integration-CI Tool

- **Tool name**: `moonmind.integration_ci`
- **Default version**: `1.0`
- **Runner profile**: `moonmind-integration-ci`
- **Command**: `./tools/test_integration.sh`
- **Workspace requirement**: `repoDir` and `artifactsDir` must stay under the configured workload workspace root.
- **Declared outputs**: The existing workload launcher publishes stdout, stderr, diagnostics, and any declared outputs through artifact refs, including failure-context artifacts emitted by the integration runner.
