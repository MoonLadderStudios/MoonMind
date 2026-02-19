# Quickstart: Dual OAuth Setup for Codex + Claude

## Prerequisites

- Docker + Docker Compose running.
- `.env` configured from `.env-template`.
- MoonMind network accessible (`local-network` by default).

## 1) Authenticate Codex volume

```bash
./tools/auth-codex-volume.sh
```

Expected result: interactive login succeeds and `codex login status` reports authenticated.

## 2) Authenticate Claude volume

```bash
./tools/auth-claude-volume.sh
```

Expected result: interactive login succeeds and Claude auth status command reports authenticated.

## 3) Configure default runtime fallback (optional)

Set in `.env`:

```env
MOONMIND_DEFAULT_TASK_RUNTIME=codex
```

Use `claude` if you want runtime-default queue tasks to execute via Claude when no runtime is specified in payload.

## 4) Start worker and verify runtime mode checks

```bash
docker compose up -d codex-worker
docker compose logs -f codex-worker
```

Validate expected preflight behavior:

- `MOONMIND_WORKER_RUNTIME=codex` checks Codex auth only.
- `MOONMIND_WORKER_RUNTIME=claude` checks Claude auth only.
- `MOONMIND_WORKER_RUNTIME=universal` checks both.

## 5) Validate tests

```bash
./tools/test_unit.sh
```

## Validation Snapshot (2026-02-19)

- `./tools/test_unit.sh`: passed (`580 passed, 216 warnings, 8 subtests passed`).
- `validate-implementation-scope --check tasks --mode runtime`: passed (`runtime tasks=3, validation tasks=6`).
- `validate-implementation-scope --check diff --mode runtime --base-ref origin/main`: passed (`runtime files=4, test files=3`).
