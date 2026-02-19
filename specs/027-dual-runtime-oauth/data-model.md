# Data Model: Dual OAuth Setup and Runtime Defaults

## Auth Volume Configuration

Represents persisted CLI auth locations used by worker startup checks and operator setup scripts.

- **Codex Auth Volume**
  - `CODEX_VOLUME_NAME`
  - `CODEX_VOLUME_PATH`
  - `CODEX_HOME`
- **Claude Auth Volume** (new)
  - `CLAUDE_VOLUME_NAME`
  - `CLAUDE_VOLUME_PATH`
  - `CLAUDE_HOME`

### Invariants

- Volume path values must be absolute in-container filesystem paths.
- Auth state must survive container restarts.
- Codex and Claude auth paths must remain independent.

## Worker Runtime Auth Profile

Runtime mode determines preflight auth requirements.

- `codex`: requires Codex auth status success.
- `claude`: requires Claude auth status success.
- `universal`: requires both Codex and Claude auth status success.

### Invariants

- Missing auth in required runtime mode blocks worker start.
- Auth checks are non-interactive in preflight.

## Default Task Runtime Setting

Deployment-level fallback runtime used only when task payload omits runtime fields.

- Field: `default_task_runtime`
- Env alias: `MOONMIND_DEFAULT_TASK_RUNTIME` (plus spec workflow alias)
- Supported values: `codex`, `gemini`, `claude`

### Invariants

- Explicit `targetRuntime` or `task.runtime.mode` overrides default fallback.
- Normalized payload must include resolved `targetRuntime` and aligned `task.runtime.mode`.
- Required capability derivation continues to include resolved runtime + `git` (+ `gh` for PR publish mode).
