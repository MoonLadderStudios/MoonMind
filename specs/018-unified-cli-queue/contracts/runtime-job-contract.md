# Contract: Runtime-Neutral Job Payload

## Purpose

Define the queue payload shape that can be consumed by any runtime worker on `moonmind.jobs`.

## Payload

```json
{
  "job_id": "uuid",
  "repo": {
    "url": "https://github.com/org/repo.git",
    "ref": "main"
  },
  "workdir": "relative/path",
  "task": {
    "goal": "Implement requirement set",
    "constraints": [
      "Do not break public API",
      "Follow repository constitution"
    ],
    "inputs": {
      "files": ["spec.md", "tasks.md"]
    },
    "outputs": {
      "pr": true,
      "artifacts": ["report.md"]
    },
    "target_runtime": "codex"
  },
  "runtime_hints": {
    "temperature": 0.2,
    "max_minutes": 30
  }
}
```

## Rules

1. Base payload fields must remain runtime-neutral.
2. `task.target_runtime` is optional and only required for targeted execution flows.
3. Queue transport remains `moonmind.jobs` regardless of `task.target_runtime`.
4. Runtime-specific command-line payload fields are out of contract.

## Validation

- Reject malformed JSON or missing `job_id`/`task.goal`.
- Reject unsupported `task.target_runtime` values when present.
- Accept payloads with no `task.target_runtime` for default runtime-neutral handling.
