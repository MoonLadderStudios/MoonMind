# Quickstart: Unified CLI Single Queue Worker Runtime

## 1. Build shared image

```bash
docker compose build api celery-worker celery_codex_worker celery_gemini_worker
```

## 2. Start broker and API dependencies

```bash
docker compose up -d rabbitmq api-db init-db api
```

## 3. Start homogeneous runtime fleet (example: codex)

```bash
MOONMIND_WORKER_RUNTIME=codex MOONMIND_QUEUE=moonmind.jobs \
  docker compose up -d celery-worker
```

## 4. Start mixed runtime fleet (codex + gemini + claude + universal)

```bash
MOONMIND_QUEUE=moonmind.jobs docker compose up -d \
  celery-worker celery_codex_worker celery_gemini_worker celery_claude_worker
```

Use `MOONMIND_WORKER_RUNTIME` overrides per service to choose homogeneous vs mixed runtime fleets.

## 5. Verify worker startup checks

Expected startup validation commands:

- `codex --version`
- `gemini --version`
- `claude --version`
- `speckit --version`

Workers should refuse job processing if any required command fails.

## 6. Run unit tests

```bash
./tools/test_unit.sh
```

## 7. Manual runtime scope validation

Repository note: `.specify/scripts/bash/validate-implementation-scope.sh` is not present.
Use manual validation:

1. Confirm production runtime files changed (`api_service/`, `moonmind/`, `celery_worker/`, `docker-compose.yaml`).
2. Confirm validation tasks completed and unit-test command executed.
3. Confirm `tasks.md` marks completed work as `[X]`.
