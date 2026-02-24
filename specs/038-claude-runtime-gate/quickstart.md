# Quickstart — Claude Runtime API-Key Gating

## 1. Configure environment
1. Copy `.env.example` if needed and ensure `ANTHROPIC_API_KEY` (or legacy `CLAUDE_API_KEY`) is either blank (to test the disabled path) or set to a valid token.
2. Optional: set `MOONMIND_WORKER_RUNTIME` or `MOONMIND_WORKER_CAPABILITIES` to `claude` when exercising worker-specific behavior.

## 2. Worker preflight smoke test
```bash
# Expect failure when no key is set
env MOONMIND_WORKER_RUNTIME=claude ANTHROPIC_API_KEY="" \
  python -m moonmind.agents.codex_worker.cli --run-preflight

# Expect success (runs `claude --version` only) when key present
env MOONMIND_WORKER_RUNTIME=claude ANTHROPIC_API_KEY=dummy \
  python -m moonmind.agents.codex_worker.cli --run-preflight
```
The first command should exit with `RuntimeError: ANTHROPIC_API_KEY must be configured when Claude runtime is enabled`. The second should finish silently.

## 3. Queue validation
```bash
# Without key, HTTP 400 is expected
uvicorn api_service.main:app --reload &
API_PID=$!
http POST :8000/api/queue/jobs type=task payload:='{"targetRuntime":"claude"}'
# Response: 400 with code=claude_runtime_disabled

# With key, the same payload succeeds
ANTHROPIC_API_KEY=dummy http POST :8000/api/queue/jobs type=task payload:='{"targetRuntime":"claude"}'
kill $API_PID
```

## 4. Dashboard runtime dropdown
1. Start the API service (`docker compose up api` or `uvicorn`).
2. Visit `/tasks` in the browser:
   - Without a key, the runtime dropdown should only list Codex and Gemini.
   - After exporting `ANTHROPIC_API_KEY`, refresh to see Claude added.

## 5. Automated tests
Run the canonical unit suite:
```bash
./tools/test_unit.sh
```
This exercises worker preflight, queue validation, dashboard config, and settings startup checks. Ensure all suites pass before shipping PR 1.
