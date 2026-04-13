# Contract: Bounded Helper Workload

This contract defines the planned internal runtime shape for Phase 7 helper containers. It is an executable workload contract, not an HTTP API and not a `MoonMind.AgentRun` contract.

## Helper Profile Contract

```json
{
  "id": "redis-helper",
  "kind": "bounded_service",
  "image": "registry.example.com/moonmind/redis-helper:7.2",
  "workdirTemplate": "/work/agent_jobs/${task_run_id}/repo",
  "requiredMounts": [
    {"type": "volume", "source": "agent_workspaces", "target": "/work/agent_jobs"}
  ],
  "envAllowlist": ["CI"],
  "networkPolicy": "bridge",
  "resources": {
    "cpu": "1",
    "memory": "512m",
    "maxCpu": "2",
    "maxMemory": "1g"
  },
  "helperTtlSeconds": 300,
  "maxHelperTtlSeconds": 900,
  "readinessProbe": {
    "type": "exec",
    "command": ["redis-cli", "ping"],
    "intervalSeconds": 1,
    "timeoutSeconds": 2,
    "retries": 10
  },
  "cleanup": {
    "removeContainerOnExit": true,
    "killGraceSeconds": 10
  },
  "devicePolicy": {"mode": "none"}
}
```

## Start Helper Request

```json
{
  "profileId": "redis-helper",
  "taskRunId": "task-123",
  "stepId": "integration-tests",
  "attempt": 1,
  "toolName": "container.start_helper",
  "repoDir": "/work/agent_jobs/task-123/repo",
  "artifactsDir": "/work/agent_jobs/task-123/artifacts/integration-tests",
  "command": ["--appendonly", "no"],
  "envOverrides": {"CI": "1"},
  "ttlSeconds": 300,
  "timeoutSeconds": 60,
  "sessionId": "session-optional-grouping",
  "sessionEpoch": 1,
  "sourceTurnId": "turn-optional-grouping"
}
```

## Start Helper Result

```json
{
  "requestId": "mm-helper-task-123-integration-tests-1",
  "profileId": "redis-helper",
  "status": "ready",
  "labels": {
    "moonmind.kind": "bounded_service",
    "moonmind.task_run_id": "task-123",
    "moonmind.step_id": "integration-tests",
    "moonmind.attempt": "1",
    "moonmind.tool_name": "container.start_helper",
    "moonmind.workload_profile": "redis-helper"
  },
  "stdoutRef": "runtime.stdout artifact ref",
  "stderrRef": "runtime.stderr artifact ref",
  "diagnosticsRef": "runtime.diagnostics artifact ref",
  "metadata": {
    "helper": {
      "containerName": "mm-helper-task-123-integration-tests-1",
      "ttlSeconds": 300,
      "status": "ready",
      "readiness": {
        "status": "ready",
        "attempts": 1
      },
      "sessionContext": {
        "sessionId": "session-optional-grouping",
        "sessionEpoch": 1,
        "sourceTurnId": "turn-optional-grouping"
      }
    }
  }
}
```

## Stop Helper Request

```json
{
  "profileId": "redis-helper",
  "taskRunId": "task-123",
  "stepId": "integration-tests",
  "attempt": 1,
  "toolName": "container.stop_helper",
  "repoDir": "/work/agent_jobs/task-123/repo",
  "artifactsDir": "/work/agent_jobs/task-123/artifacts/integration-tests",
  "command": ["stop"],
  "ttlSeconds": 300,
  "reason": "bounded_window_complete"
}
```

## Stop Helper Result

```json
{
  "requestId": "mm-helper-task-123-integration-tests-1",
  "profileId": "redis-helper",
  "status": "stopped",
  "diagnosticsRef": "runtime.diagnostics artifact ref",
  "metadata": {
    "helper": {
      "containerName": "mm-helper-task-123-integration-tests-1",
      "status": "stopped",
      "teardown": {
        "status": "complete",
        "reason": "bounded_window_complete",
        "removeContainerOnExit": true
      }
    }
  }
}
```

## Policy Denials

Helper policy denials must be stable and non-secret. Required denial reasons include:

- `unknown_profile`
- `unsupported_helper_profile`
- `missing_helper_ttl`
- `resource_request_too_large`
- `disallowed_env_key`
- `disallowed_mount`
- `readiness_failed`
- `helper_timeout`

## Artifact Classes

- `runtime.stdout`: bounded helper stdout.
- `runtime.stderr`: bounded helper stderr.
- `runtime.diagnostics`: helper profile, ownership, TTL, readiness, teardown, and artifact publication metadata.
- Declared output classes: profile/tool-specific outputs under the owner step artifacts directory.

## Guardrails

- Helper containers are workload containers, not managed sessions.
- Optional session context is grouping metadata only.
- Helper state in Docker is not durable truth.
- Raw secrets, prompts, transcripts, scrollback, credentials, and unbounded logs must not appear in metadata.
