# Data Model Notes: Claude Runtime API-Key Gate

## Configuration

- `ANTHROPIC_API_KEY` (alias: `CLAUDE_API_KEY`)
  - Source-of-truth switch for Claude availability.
- `supportedTaskRuntimes`
  - Produced by server config builder; contains `claude` only when key is available.
- `defaultTaskRuntime`
  - Normalized runtime fallback used by API defaults.
  - Must not resolve to `claude` when key is unavailable.

## Validation Rule

If runtime is resolved to `claude` and the key is missing:

- Reject queue task normalization with a 400 validation message:
  - `targetRuntime=claude requires ANTHROPIC_API_KEY to be configured`
