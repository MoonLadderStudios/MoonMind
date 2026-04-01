# Spec 121: Canonical Model Default Precedence

## Summary

MoonMind supports one consistent model-resolution chain for managed agent runs:

```
task-selected model → provider profile default model → runtime default model
```

This ensures every managed agent invocation uses a well-defined, traceable model regardless of whether the operator has explicitly configured one.

## Target Runtime Defaults

| Runtime ID (canonical) | Default Model |
|---|---|
| `codex_cli` | `gpt-5.4` |
| `gemini_cli` | `gemini-3.1-pro-preview` |
| `claude_code` | `Sonnet 4.6` |

## Runtime ID Aliases

| Alias | Canonical |
|---|---|
| `codex` | `codex_cli` |
| `claude` | `claude_code` |

## Model Source Values

| Source | Meaning |
|---|---|
| `task_override` | Explicitly selected on the task |
| `provider_profile_default` | From the selected provider profile's `default_model` |
| `runtime_default` | From the canonical runtime registry |
| `none` | No model could be resolved |

## API Contract

`ExecutionModel` exposes:
- `model` — resolved model (backward compat)
- `resolvedModel` — alias for resolved model
- `requestedModel` — what the task originally asked for (`null` if none)
- `modelSource` — one of the source values above
- `profileId` — the provider profile selected for this run

## Provider Profile Behavior

- `default_model = null` → inherits runtime default (standard seeded profiles)
- `default_model = "some-model"` → overrides runtime default for that profile
- `claude_minimax` always carries explicit `default_model = "MiniMax-M2.7"`
