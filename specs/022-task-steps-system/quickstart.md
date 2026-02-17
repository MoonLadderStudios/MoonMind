# Quickstart: Validate Task Steps System

## 1. Run focused unit suites during development

```bash
./tools/test_unit.sh tests/unit/workflows/agent_queue/test_task_contract.py tests/unit/agents/codex_worker/test_worker.py
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py tests/unit/api/routers/test_task_dashboard.py
```

## 2. Run full unit regression

```bash
./tools/test_unit.sh
```

## 3. Validate Spec Kit scope gates

```bash
.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

## 4. Manual queue task payload smoke example with steps

```json
{
  "type": "task",
  "priority": 0,
  "maxAttempts": 3,
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "targetRuntime": "codex",
    "task": {
      "instructions": "Implement feature and verify tests.",
      "runtime": { "mode": "codex", "model": "gpt-5-codex", "effort": "high" },
      "skill": { "id": "auto", "args": {} },
      "steps": [
        { "id": "inspect", "instructions": "Inspect existing implementation." },
        { "id": "patch", "instructions": "Apply code changes.", "skill": { "id": "speckit", "args": {} } },
        { "id": "verify", "instructions": "Run and fix failing unit tests." }
      ],
      "publish": { "mode": "pr" }
    }
  }
}
```

## 5. Latest validation results (2026-02-17)

- Unit test regression:
  - `./tools/test_unit.sh tests/unit/workflows/agent_queue/test_task_contract.py tests/unit/agents/codex_worker/test_worker.py`
  - Result: `468 passed, 195 warnings`
- Scope gates:
  - `Scope validation passed: tasks check (runtime tasks=12, validation tasks=9).`
  - `Scope validation passed: diff check (runtime files=12, test files=9).`
