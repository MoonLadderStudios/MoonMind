# Quickstart: Dashboard Queue Task Default Pre-Population

## 1. Prerequisites

- API service and queue worker running.
- Auth configured for dashboard access.

## 2. Open Queue Submit Form

Visit `/tasks/queue/new`.

Expected pre-populated inputs:

- Runtime: `codex`
- Model: `gpt-5.3-codex`
- Effort: `high`
- Repository: `MoonLadderStudios/MoonMind`

## 3. Manual Validation

1. Submit a task without modifying pre-populated runtime/model/effort/repository values; verify created payload contains non-empty resolved values.
2. Clear model and effort inputs, submit again, and verify backend resolves to default model/effort.
3. Change runtime/model/effort/repository to custom values and submit; verify payload preserves overrides.
4. Update relevant settings/env values, restart API, reload `/tasks/queue/new`, and verify new defaults appear.

## 4. Automated Validation

Run:

```bash
./tools/test_unit.sh
```

Ensure tests for settings defaults, dashboard runtime config, and queue service default resolution pass.
