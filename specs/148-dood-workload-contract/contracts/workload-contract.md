# Workload Contract

## Scope

This contract covers Phase 1 validation and serialization only. It does not launch Docker containers and does not define executable tool wiring.

## Registry File Shape

The deployment-owned registry file may be JSON or YAML. It may contain either a top-level `profiles` list or a mapping keyed by profile ID.

```yaml
profiles:
  - id: local-python
    kind: one_shot
    image: python:3.12-slim
    entrypoint: ["/bin/bash", "-lc"]
    workdir_template: /work/agent_jobs/${task_run_id}/repo
    required_mounts:
      - type: volume
        source: agent_workspaces
        target: /work/agent_jobs
    env_allowlist:
      - CI
    network_policy: none
    resources:
      cpu: "2"
      memory: "2g"
      shm_size: "512m"
      max_cpu: "4"
      max_memory: "4g"
      max_shm_size: "1g"
    timeout_seconds: 300
    max_timeout_seconds: 600
    cleanup:
      remove_container_on_exit: true
      kill_grace_seconds: 30
    device_policy:
      mode: none
```

## Workload Request Shape

```json
{
  "profileId": "local-python",
  "taskRunId": "task-1",
  "stepId": "step-test",
  "attempt": 1,
  "toolName": "container.run_workload",
  "repoDir": "/work/agent_jobs/task-1/repo",
  "artifactsDir": "/work/agent_jobs/task-1/artifacts/step-test",
  "command": ["pytest", "-q"],
  "envOverrides": {"CI": "1"},
  "timeoutSeconds": 300,
  "resources": {"cpu": "2", "memory": "2g"},
  "sessionId": "session-1",
  "sessionEpoch": 2,
  "sourceTurnId": "turn-1"
}
```

## Required Labels

The registry validator must derive these labels for every validated request:

- `moonmind.kind=workload`
- `moonmind.task_run_id=<task_run_id>`
- `moonmind.step_id=<step_id>`
- `moonmind.attempt=<attempt>`
- `moonmind.tool_name=<tool_name>`
- `moonmind.workload_profile=<profile_id>`

Optional session labels may be included when the request carries session association metadata.

## Validation Errors

Validation must fail before Docker launch code for:

- unknown profile ID
- blank or unpinned image
- `latest` image tag
- host networking
- privileged or GPU device policy in Phase 1
- non-volume or unsafe mount targets
- environment override key outside the profile allowlist
- workspace path outside `workspace_root`
- resource override above profile maximum
- timeout override above profile maximum
