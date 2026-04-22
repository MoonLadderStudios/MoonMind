# Contract: Workflow Docker Access Tool Boundary

## Setting

```text
MOONMIND_WORKFLOW_DOCKER_ENABLED=true|false
```

- Default: `true`
- Scope: workflow-requested Docker-backed workload tools and direct `workload.run` activity calls.
- Disabled failure code: `docker_workflows_disabled`

## Curated Tool

```json
{
  "name": "moonmind.integration_ci",
  "version": "1.0",
  "type": "skill",
  "requirements": {
    "capabilities": ["docker_workload"]
  },
  "executor": {
    "activity_type": "mm.tool.execute",
    "selector": {"mode": "by_capability"}
  }
}
```

## Inputs

Required:
- `repoDir`: Docker-visible repository workspace.
- `artifactsDir`: Docker-visible artifact directory.

Optional:
- `taskRunId`
- `stepId`
- `attempt`
- `timeoutSeconds`
- `envOverrides`
- `resources`
- `declaredOutputs`
- `sessionId`
- `sessionEpoch`
- `sourceTurnId`

The tool ignores caller-supplied `profileId` and `command`; it always uses runner profile `moonmind-integration-ci` and command `./tools/test_integration.sh`.

## Outputs

The tool returns the existing workload result envelope:

```json
{
  "workloadResult": {},
  "requestId": "string",
  "profileId": "moonmind-integration-ci",
  "workloadStatus": "succeeded|failed|timed_out|canceled",
  "exitCode": 0,
  "stdoutRef": "artifact-ref-or-null",
  "stderrRef": "artifact-ref-or-null",
  "diagnosticsRef": "artifact-ref-or-null",
  "outputRefs": {},
  "workloadMetadata": {}
}
```

## Failure

When `MOONMIND_WORKFLOW_DOCKER_ENABLED=false`, the tool fails before registry validation or launcher invocation:

```json
{
  "error_code": "PERMISSION_DENIED",
  "details": {
    "reason": "docker_workflows_disabled"
  }
}
```
