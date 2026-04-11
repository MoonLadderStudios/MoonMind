# Contract: DooD Executable Tools

This contract defines the Phase 3 executable tool surface for Docker-backed workloads. These are tool-registry contracts, not HTTP API endpoints.

## Tool: `container.run_workload`

### ToolDefinition

```yaml
name: container.run_workload
version: "1.0"
type: skill
description: Run one policy-gated Docker workload through MoonMind.
executor:
  activity_type: mm.tool.execute
  selector:
    mode: by_capability
requirements:
  capabilities:
    - docker_workload
policies:
  timeouts:
    start_to_close_seconds: 3600
    schedule_to_close_seconds: 3900
  retries:
    max_attempts: 1
    non_retryable_error_codes:
      - INVALID_INPUT
      - PERMISSION_DENIED
security:
  allowed_roles:
    - user
    - admin
```

### Inputs

```json
{
  "profileId": "local-python",
  "repoDir": "/work/agent_jobs/task-1/repo",
  "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-1",
  "command": ["python", "-V"],
  "envOverrides": {"CI": "1"},
  "timeoutSeconds": 300,
  "resources": {
    "cpu": "1",
    "memory": "512m",
    "shmSize": "256m"
  },
  "sessionId": "session-1",
  "sessionEpoch": 1,
  "sourceTurnId": "turn-1"
}
```

### Input Rules

- `profileId`, `repoDir`, `artifactsDir`, and `command` are required.
- `taskRunId`, `stepId`, and `attempt` may be supplied explicitly or derived from execution context.
- `envOverrides` is accepted only after runner-profile allowlist validation.
- Raw image, mount, device, privileged, Docker socket, host networking, and arbitrary Docker options are not accepted.

## Tool: `unreal.run_tests`

### ToolDefinition

```yaml
name: unreal.run_tests
version: "1.0"
type: skill
description: Run Unreal Engine tests through a curated runner profile.
executor:
  activity_type: mm.tool.execute
  selector:
    mode: by_capability
requirements:
  capabilities:
    - docker_workload
policies:
  timeouts:
    start_to_close_seconds: 3600
    schedule_to_close_seconds: 3900
  retries:
    max_attempts: 1
    non_retryable_error_codes:
      - INVALID_INPUT
      - PERMISSION_DENIED
security:
  allowed_roles:
    - user
    - admin
```

### Inputs

```json
{
  "repoDir": "/work/agent_jobs/task-1/repo",
  "artifactsDir": "/work/agent_jobs/task-1/artifacts/unreal-tests",
  "projectPath": "Game/Game.uproject",
  "target": "Editor",
  "testSelector": "Project.Functional",
  "timeoutSeconds": 1800
}
```

### Input Rules

- `repoDir`, `artifactsDir`, and `projectPath` are required.
- `profileId` is optional and defaults to the curated Unreal runner profile.
- `target` and `testSelector` are optional domain selectors.
- The tool converts domain fields into the curated workload command before validating the final `WorkloadRequest`.
- Raw Docker parameters are not accepted.

## Shared Output

Both tools return a normal `ToolResult` payload:

```json
{
  "status": "COMPLETED",
  "outputs": {
    "workloadResult": {
      "requestId": "mm-workload-task-1-step-1-1",
      "profileId": "local-python",
      "status": "succeeded",
      "exitCode": 0,
      "labels": {
        "moonmind.kind": "workload",
        "moonmind.task_run_id": "task-1",
        "moonmind.step_id": "step-1",
        "moonmind.attempt": "1",
        "moonmind.tool_name": "container.run_workload",
        "moonmind.workload_profile": "local-python"
      }
    },
    "requestId": "mm-workload-task-1-step-1-1",
    "profileId": "local-python",
    "workloadStatus": "succeeded",
    "exitCode": 0,
    "outputRefs": {}
  },
  "progress": {
    "profileId": "local-python",
    "workloadStatus": "succeeded"
  }
}
```

## Routing Contract

- A pinned plan node with `tool.type = "skill"` and one of these tool names resolves through the executable tool registry.
- `docker_workload` capability routes to the existing `agent_runtime` task queue.
- `MoonMind.Run` executes the step as `mm.tool.execute` or `mm.skill.execute`; it does not start `MoonMind.AgentRun` for the workload.
- Managed-session metadata, when present, is passed as association context only.

## Validation Strategy

- Unit tests for generated tool definitions and disallowed raw Docker inputs.
- Unit tests for tool input to `WorkloadRequest` conversion.
- Unit tests for launcher invocation and `WorkloadResult` to `ToolResult` mapping.
- Activity catalog tests for `docker_workload` routing.
- Worker runtime tests for handler registration only on the agent-runtime fleet.
- Workflow-boundary tests proving `MoonMind.Run` routes a DooD tool step through `mm.tool.execute` on the agent-runtime task queue.
