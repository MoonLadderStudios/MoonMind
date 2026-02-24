# Contract — Task Dashboard Runtime Config

## Endpoint
`GET /api/task-dashboard/config` (internally uses `build_runtime_config(initial_path)`)

## Response Shape (excerpt)
```json
{
  "system": {
    "supportedTaskRuntimes": ["codex", "gemini"],
    "defaultTaskRuntime": "codex",
    "defaultTaskModel": "gpt-5.3-codex",
    "defaultTaskEffort": "high",
    "supportedWorkerRuntimes": ["codex", "gemini", "claude", "universal"],
    ...
  }
}
```

## Claude-enabled Variant
When `RuntimeGateState.enabled` is true, the only change is:
```json
"supportedTaskRuntimes": ["codex", "gemini", "claude"],
"defaultTaskRuntime": "claude" | "codex" | "gemini"  // whichever matches env/settings and exists in the list
```

Rules:
1. `supportedTaskRuntimes` always contains codex and gemini; claude is appended only if the gate is enabled.
2. `defaultTaskRuntime` resolves in order: `MOONMIND_WORKER_RUNTIME`, `settings.workflow.default_task_runtime`, fallback to `supportedTaskRuntimes[0]`. Any value not in the supported list is ignored.
3. Downstream UI must treat the list as authoritative; no client-side filtering is required once this response is honored.
