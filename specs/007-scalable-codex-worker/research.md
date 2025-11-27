# Research: Scalable Codex Worker

**Feature**: Scalable Codex Worker (007)
**Date**: 2025-11-27

## Decision Log

### 1. Docker Volume for Authentication
- **Decision**: Use a named Docker volume `codex_auth_volume` mounted to `/home/app/.codex` (or equivalent user home).
- **Rationale**: Allows persistence of OAuth tokens across container restarts and sharing among scaled worker replicas.
- **Implementation**: 
  - Define `codex_auth_volume` in `docker-compose.yaml`.
  - Mount to worker service: `volumes: - codex_auth_volume:/home/app/.codex`.
  - Setup requires a one-off run: `docker compose run --rm celery_codex_worker /bin/bash`.

### 2. Celery Queue Isolation
- **Decision**: Use the `-Q` flag to bind the worker exclusively to the `codex` queue.
- **Rationale**: Prevents the Codex worker from consuming general tasks and prevents general workers from consuming Codex tasks (assuming general workers are configured with `-Q celery` or exclude `codex`).
- **Implementation**: `command: celery -A celery_worker.speckit_worker worker -l info -Q codex -c 1` (concurrency 1 to start).

### 3. Pre-flight Auth Check
- **Decision**: Implement a startup script that runs `codex whoami` (or equivalent API ping).
- **Rationale**: Fails fast if the volume is unauthenticated, preventing "zombie" workers.
- **Implementation**: Wrap the celery command in an entrypoint script or chain commands: `codex whoami && celery ...`.

### 4. Non-Interactive Configuration
- **Decision**: Pre-provision `config.toml` with `approval_policy = "never"` in the image or via a setup script if not present.
- **Rationale**: Ensures strict non-interactive mode for automation.
- **Implementation**: The Spec Kit CLI or Codex CLI should respect this config. We will verify `~/.codex/config.toml` has this setting during the pre-flight check or enforce it via environment variables if supported (e.g., `CODEX_APPROVAL_POLICY=never`).
