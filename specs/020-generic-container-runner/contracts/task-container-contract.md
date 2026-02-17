# Contract: Task Container Execution

## 1. Queue Payload Contract

`task.container` object (optional):

```json
{
  "enabled": true,
  "image": "mcr.microsoft.com/dotnet/sdk:8.0",
  "command": ["bash", "-lc", "dotnet test ./MySolution.sln"],
  "workdir": "/work/agent_jobs/<job_id>/repo",
  "env": {
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1"
  },
  "artifactsSubdir": "container",
  "timeoutSeconds": 3600,
  "resources": {
    "cpus": 8,
    "memory": "16g"
  },
  "pull": "if-missing",
  "cacheVolumes": [
    {"name": "nuget_cache", "target": "/home/app/.nuget/packages"}
  ]
}
```

Validation rules:

- If `enabled=true`, `image` and non-empty `command` are required.
- `command` must be array-of-strings.
- `requiredCapabilities` must include `docker` for routable execution.

## 2. Worker Execution Contract

When `task.container.enabled=true`:

1. Worker emits `moonmind.task.container.started`.
2. Worker performs configured pull behavior.
3. Worker executes one ephemeral container with deterministic name and labels.
4. Worker enforces timeout and performs best-effort stop/cleanup.
5. Worker writes metadata/log artifacts.
6. Worker emits `moonmind.task.container.finished` including exit/timeout status.

## 3. Artifact Contract

Minimum outputs:

- `logs/execute.log` (worker stage log)
- `container/metadata/run.json`

Optional outputs (runner-written):

- `container/logs/runner.log`
- `container/test-results/*.xml`
- Other tool-specific outputs
