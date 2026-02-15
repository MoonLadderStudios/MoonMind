# Quickstart: Scalable Codex Worker (015-Aligned)

## Goal

Bring up Codex + Gemini workers with:

- persistent Codex authentication via shared volume
- Speckit capability checks on startup
- Google Gemini embedding defaults validated before task execution
- skills-first workflow metadata preserved for stage execution

## Prerequisites

- Docker + Docker Compose
- `.env` copied from `.env-template`
- Required environment values:
  - `GOOGLE_API_KEY`
  - `DEFAULT_EMBEDDING_PROVIDER=google`
  - `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
  - `CODEX_ENV=prod`
  - `CODEX_MODEL=gpt-5-codex`
  - `GITHUB_TOKEN`
  - `CODEX_VOLUME_NAME=codex_auth_volume` (default)

## 1) Authenticate Codex volume (one-time)

```bash
docker compose run --rm celery_codex_worker \
  bash -lc 'codex login --device-auth && codex login status'
```

## 2) Start runtime services

```bash
docker compose up -d rabbitmq api celery_codex_worker celery_gemini_worker
```

## 3) Verify startup readiness

```bash
docker compose logs --tail=200 celery_codex_worker
docker compose logs --tail=200 celery_gemini_worker
```

Expected log signals:

- `Codex CLI detected ...` (codex worker)
- `Gemini CLI detected ...` (gemini worker)
- `Spec Kit CLI detected ...` (both workers)
- queue binding log lines for each worker
- Codex pre-flight success on codex worker
- embedding runtime profile diagnostics

## 4) Verify queue bindings and optional scaling

```bash
docker compose up -d --scale celery_codex_worker=2
docker compose ps celery_codex_worker
```

Codex workers should still consume configured `speckit` + `codex` queues.

## 5) Verify embedding defaults from API settings

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

## 6) Validate workflow API compatibility

```bash
curl http://localhost:5000/api/workflows/speckit/runs
```

Task payloads in run detail continue to include stage execution metadata:

- `selectedSkill`
- `executionPath`
- `usedSkills`
- `usedFallback`
- `shadowModeRequested`

## 7) Unit validation gate

```bash
./tools/test_unit.sh
```
