# Contract: Queue Task Defaults and Dashboard Pre-Population

## 1. Dashboard Runtime Config Contract

`GET /tasks*` rendered config payload must include these keys under `system`:

- `defaultTaskRuntime` (string, supported runtime)
- `defaultTaskModel` (string)
- `defaultTaskEffort` (string)
- `defaultRepository` (string, token-free `owner/repo`, `https://...`, or `git@...`)

## 2. Queue Submit UI Contract

Queue submit form (`/tasks/queue/new`) must:

1. Pre-populate runtime/model/effort/repository inputs from runtime config values.
2. Keep all pre-populated fields editable before submit.
3. Submit edited values as explicit payload overrides.
4. Allow empty user input and rely on backend default resolution for omitted fields.

## 3. Queue API Default Resolution Contract

For `POST /api/queue/jobs` with `type="task"`:

1. If `payload.repository` is omitted/blank, backend resolves from settings default repository.
2. If runtime mode is omitted, backend resolves to default runtime (`codex`).
3. If runtime mode resolves to `codex` and `task.runtime.model` is omitted, backend resolves to settings default codex model.
4. If runtime mode resolves to `codex` and `task.runtime.effort` is omitted, backend resolves to settings default codex effort.
5. Explicit client values for runtime/model/effort/repository MUST remain unchanged.

## 4. Baseline Default Values

When no explicit environment overrides exist, baseline defaults are:

- Runtime: `codex`
- Model: `gpt-5.3-codex`
- Effort: `high`
- Repository: `MoonLadderStudios/MoonMind`
