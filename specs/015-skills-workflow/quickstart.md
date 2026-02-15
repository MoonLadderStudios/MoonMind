# Quickstart: Skills-First Workers with Codex Auth + Gemini Embeddings

## Prerequisites

- Docker and Docker Compose available.
- `.env` created from `.env-template`.
- Required values set:
  - `GOOGLE_API_KEY`
  - `DEFAULT_EMBEDDING_PROVIDER=google`
  - `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
  - `CODEX_ENV=prod`
  - `CODEX_MODEL=gpt-5-codex`
  - `GITHUB_TOKEN`

## 1) Authenticate Codex worker volume (one-time)

```bash
docker compose run --rm celery_codex_worker \
  bash -lc 'codex login && codex login status'
```

This stores auth in `${CODEX_VOLUME_NAME:-codex_auth_volume}`.

## 2) Start runtime services

```bash
docker compose up -d rabbitmq api qdrant celery_codex_worker celery_gemini_worker
```

## 3) Verify Codex worker readiness

```bash
docker compose logs --tail=200 celery_codex_worker
```

Expected log signals:

- queue bindings include `speckit` and `codex`
- Codex preflight check succeeds
- startup does not block on interactive approval/login prompts

## 4) Verify Gemini worker readiness

```bash
docker compose logs --tail=200 celery_gemini_worker
```

Expected log signals:

- queue bindings include `gemini`
- Gemini worker startup completes without CLI/auth errors

## 5) Verify embedding runtime defaults

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

## 6) Run unit validation gate

```bash
./tools/test_unit.sh
```
