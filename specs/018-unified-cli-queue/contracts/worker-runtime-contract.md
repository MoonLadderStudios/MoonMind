# Contract: Worker Runtime Selection and Startup Health

## Purpose

Define startup behavior for runtime mode selection, CLI health checks, and queue binding.

## Environment Inputs

- `MOONMIND_WORKER_RUNTIME`: `codex|gemini|claude|universal`.
- `MOONMIND_QUEUE`: default `moonmind.jobs`.
- Legacy compatibility vars (transitional only):
  - `SPEC_WORKFLOW_CODEX_QUEUE`
  - `GEMINI_CELERY_QUEUE`

## Startup Contract

1. Worker reads and validates `MOONMIND_WORKER_RUNTIME`.
2. Worker validates CLI binaries:
   - `codex --version`
   - `gemini --version`
   - `claude --version`
   - `speckit --version`
3. Worker binds to `MOONMIND_QUEUE`/effective queue.
4. Worker exits before polling if runtime mode or CLI checks fail.

## Runtime Execution Contract

1. Runtime-neutral jobs are executable by any runtime mode worker.
2. Universal mode may use optional target runtime metadata to dispatch internally.
3. Queue-level routing must not be required for runtime targeting.

## Security Contract

1. Auth material is runtime injected (volumes, env, secret providers).
2. No runtime tokens or credentials are baked into image layers.
