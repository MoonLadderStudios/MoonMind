# Quickstart: Skills Workflow Fast Path (Codex + Gemini)

## Prerequisites

- Docker and Docker Compose installed.
- `.env` copied from `.env-template`.
- Required runtime values set:
  - `CODEX_ENV=prod`
  - `CODEX_MODEL=gpt-5.3-codex`
  - `GITHUB_TOKEN=<repo-capable token>`
  - `DEFAULT_EMBEDDING_PROVIDER=google`
  - `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
  - `GOOGLE_API_KEY=<key>` (or `GEMINI_API_KEY`)

## 1) Authenticate worker volumes (one-time per environment)

```bash
./tools/auth-codex-volume.sh
./tools/auth-gemini-volume.sh
```

This persists auth state in `codex_auth_volume` and `gemini_auth_volume` for startup preflight checks.

## 2) Start core runtime services

```bash
docker compose up -d rabbitmq api codex-worker gemini-worker
```

## 3) Confirm whether configured stage skills require Agentkit checks

```bash
docker compose exec api python - <<'PY'
from moonmind.workflows.skills.registry import configured_stage_skills_require_agentkit
print(configured_stage_skills_require_agentkit())
PY
```

Expected output:

- `True` when at least one configured stage skill resolves to a Agentkit adapter.
- `False` when stage skills are configured to non-Agentkit adapters.

## 4) Verify codex-worker readiness

```bash
docker compose logs --tail=200 codex-worker
```

Expected signals:

- runtime mode resolves and queue bindings are logged
- Agentkit CLI check appears only when Step 3 returned `True`
- shared skills mirror validation logs appear when strict mode is enabled

## 5) Verify gemini-worker readiness

```bash
docker compose logs --tail=200 gemini-worker
```

Expected signals:

- Gemini CLI auth mode resolution is logged.
- Gemini preflight check completes.
- Shared skills mirror validation logs appear when strict mode is enabled.

## 6) Verify embedding defaults

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

## 7) Verify runtime-mode unit test gate

```bash
./tools/test_unit.sh
```

For this feature, orchestration mode is `runtime`, so this validation step is required (docs-only completion is out of scope).
