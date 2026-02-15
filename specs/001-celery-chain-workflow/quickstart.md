# Quickstart: Celery Chain Workflow Integration (Skills-First Alignment)

## Goal

Start MoonMind workflow services on the fastest path so Celery stages run with:

- persistent Codex authentication,
- Speckit always available on workers,
- Google Gemini embeddings as the vector default.

## Prerequisites

- Docker and Docker Compose.
- `.env` created from `.env-template`.
- Required `.env` values:
  - `GOOGLE_API_KEY=<your_google_api_key>`
  - `DEFAULT_EMBEDDING_PROVIDER=google`
  - `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
  - `CODEX_ENV=prod`
  - `CODEX_MODEL=gpt-5-codex`
  - `GITHUB_TOKEN=<token_with_repo_access>`
  - `CODEX_VOLUME_NAME=codex_auth_volume` (or your override)
- Optional skills policy overrides (defaults keep Speckit-first parity):
  - `SPEC_WORKFLOW_USE_SKILLS=true`
  - `SPEC_WORKFLOW_DEFAULT_SKILL=speckit`
  - `SPEC_WORKFLOW_ALLOWED_SKILLS=speckit`

## Fastest Path (Docker Compose)

1. **Authenticate Codex once on the shared volume**
   ```bash
   docker compose run --rm celery_codex_worker \
     bash -lc 'codex login --device-auth && codex login status'
   ```
   This stores auth in `${CODEX_VOLUME_NAME:-codex_auth_volume}` so worker preflight can pass after restarts.

2. **Start runtime services**
   ```bash
   docker compose up -d rabbitmq api qdrant celery_codex_worker celery_gemini_worker
   ```

3. **Verify Codex worker readiness**
   ```bash
   docker compose logs --tail=200 celery_codex_worker
   ```
   Expected signals:
   - Spec Kit CLI detection logs are present.
   - Codex preflight status is `passed`.
   - Queue bindings include `speckit` and `codex`.

4. **Verify Gemini worker readiness**
   ```bash
   docker compose logs --tail=200 celery_gemini_worker
   ```
   Expected signals:
   - Gemini CLI and Spec Kit CLI detection logs are present.
   - Queue binding includes `gemini`.
   - No auth/credential startup failures.

5. **Verify embedding defaults**
   ```bash
   docker compose exec api python - <<'PY'
   from moonmind.config.settings import settings
   print(settings.default_embedding_provider)
   print(settings.google.google_embedding_model)
   PY
   ```
   Expected output:
   ```text
   google
   gemini-embedding-001
   ```

## Trigger and Observe a Workflow

1. **Submit a run**
   ```bash
   curl -X POST http://localhost:5000/api/workflows/speckit/runs \
     -H 'Content-Type: application/json' \
     -d '{
       "repository": "moonmind/spec-kit-reference",
       "featureKey": "spec-42-refresh-docs"
     }'
   ```

2. **Watch task timeline**
   ```bash
   curl http://localhost:5000/api/workflows/speckit/runs/{run_id}/tasks | jq
   ```
   Stage payloads should include:
   - `selectedSkill` (default `speckit` unless overridden),
   - `executionPath` (`skill`, `direct_fallback`, or `direct_only`).

3. **Inspect artifacts**
   ```bash
   curl http://localhost:5000/api/workflows/speckit/runs/{run_id}/artifacts | jq
   ls -la var/artifacts/spec_workflows/{run_id}
   ```

## Validation Gate

Run required unit checks:

```bash
./tools/test_unit.sh
```
