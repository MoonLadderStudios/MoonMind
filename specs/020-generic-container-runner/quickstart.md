# Quickstart: Generic Task Container Runner

## 1. Configure worker runtime for Docker-backed task execution

Example `docker-compose.yaml` worker env:

- `DOCKER_HOST=tcp://docker-proxy:2375`
- `MOONMIND_WORKDIR=/work/agent_jobs`
- `MOONMIND_WORKER_CAPABILITIES=codex,git,gh,docker`

## 2. Submit a task with `task.container`

Example payload fragment:

```json
{
  "repository": "MoonLadderStudios/MoonMind",
  "requiredCapabilities": ["codex", "git", "docker"],
  "targetRuntime": "codex",
  "task": {
    "instructions": "Run repository verification in container",
    "runtime": {"mode": "codex"},
    "publish": {"mode": "none"},
    "container": {
      "enabled": true,
      "image": "mcr.microsoft.com/dotnet/sdk:8.0",
      "command": ["bash", "-lc", "dotnet --info"],
      "timeoutSeconds": 120,
      "artifactsSubdir": "container"
    }
  }
}
```

## 3. Verify expected behavior

- Queue events include `moonmind.task.container.started` and `moonmind.task.container.finished`.
- `logs/execute.log` is uploaded.
- `container/metadata/run.json` is uploaded.
- Task terminal status matches container exit code/timeout outcome.

## 4. Run unit tests

```bash
./tools/test_unit.sh
```
