# Runtime Contract: Compose Fast Path (Codex + Gemini Workers)

## Required Environment Variables

- `CODEX_ENV=prod`
- `CODEX_MODEL=gpt-5.3-codex`
- `GITHUB_TOKEN=<repo-capable token>`
- `DEFAULT_EMBEDDING_PROVIDER=google`
- `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
- `GOOGLE_API_KEY=<key>` (or `GEMINI_API_KEY`)

## Worker/Auth Contract

- Operators authenticate worker volumes with:
  - `./tools/auth-codex-volume.sh`
  - `./tools/auth-gemini-volume.sh`
- `codex-worker` uses persisted Codex auth volume and enforces preflight checks for codex/universal runtime modes.
- `gemini-worker` uses persisted Gemini auth volume/home and enforces runtime-specific preflight checks.
- When auth is enabled, set `MOONMIND_WORKER_TOKEN` (recommended) or `MOONMIND_API_TOKEN` for queue worker/API auth.

## Queue Contract

- `codex-worker` handles workflow queues including default and codex queues per Celery configuration.
- `gemini-worker` handles the configured gemini queue.
- Queue compatibility aliases remain supported through settings.

## Skills Workspace Contract

- Run workspaces materialize one `skills_active` directory.
- `.agents/skills` and `.gemini/skills` must resolve to that same `skills_active` directory.
- Mirror resolution checks local mirror root first (`.agents/skills/local`) and legacy shared mirror root second (`.agents/skills`, including nested `/skills` compatibility).

## Stage Verification Contract

- Speckit CLI verification is conditional:
  - execute verification when selected/configured stage skills resolve to the Speckit adapter and skills mode is active,
  - skip Speckit CLI checks when stages run through non-Speckit adapters or direct-only mode.
- Stage names in logs and payloads are canonical runtime task names:
  - `discover_next_phase`
  - `submit_codex_job`
  - `apply_and_publish`

## Orchestration Mode Contract

- This feature is planned and executed in `runtime` mode.
- Runtime mode requires:
  - at least one production code change under `moonmind/` or `api_service/`,
  - unit validation through `./tools/test_unit.sh`.
- Docs-only updates may accompany runtime mode but cannot satisfy completion criteria by themselves.

## Verification Contract

Operator validation includes:

1. Worker auth scripts complete without interactive failures.
2. Worker logs show runtime mode resolution and preflight success, including conditional Speckit checks.
3. API runtime resolves embedding provider/model to expected defaults.
4. Unit validation passes via `./tools/test_unit.sh`.
