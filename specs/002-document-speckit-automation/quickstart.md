# Quickstart: Skills-First Spec Automation

## Goal

Run Spec Automation with umbrella-015 aligned defaults:

- Codex auth persisted on a shared volume
- Speckit capability verified on worker startup
- Google Gemini embedding defaults active
- API run detail exposing skills-path metadata per phase

## Prerequisites

- Docker + Docker Compose
- `.env` copied from `.env-template`
- Required values:
  - `GOOGLE_API_KEY`
  - `DEFAULT_EMBEDDING_PROVIDER=google`
  - `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
  - `CODEX_ENV=prod`
  - `CODEX_MODEL=gpt-5-codex`
  - `GITHUB_TOKEN`
  - `CODEX_VOLUME_NAME` (defaults to `codex_auth_volume`)

## 1) One-time Codex auth for worker volume

```bash
docker compose run --rm celery_codex_worker \
  bash -lc 'codex login && codex login status'
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

Expected:

- Speckit CLI checks pass on both workers.
- Codex preflight passes on codex worker.
- No interactive authentication prompts.

## 4) Verify embedding defaults

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

## 5) Verify Spec Automation API telemetry shape

```bash
curl http://localhost:5000/api/spec-automation/runs/<run_id>
```

Each phase entry now includes skills metadata fields:

- `selected_skill`
- `execution_path`
- `used_skills`
- `used_fallback`
- `shadow_mode_requested`

Legacy Speckit phase records default to `selected_skill=speckit` and `execution_path=skill` when explicit metadata is absent.

## 6) Validation gate

```bash
./tools/test_unit.sh
```
