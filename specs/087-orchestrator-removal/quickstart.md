# Quickstart: Verify orchestrator removal

**Feature**: 087-orchestrator-removal

## Prerequisites

- Docker Compose, Poetry environment as in repo README.

## Steps

1. **Unit tests**: From repo root run `./tools/test_unit.sh` — expect full pass.

2. **Compose** (optional smoke): `docker compose config` succeeds; `docker compose up -d` starts API without an `orchestrator` service in the default profile.

3. **Grep sanity** (optional): No `moonmind.workflows.orchestrator` imports outside deleted tree; no FastAPI router mounting `orchestrator`.

4. **Migrations**: `alembic upgrade head` (or project-documented migration path) applies cleanly on a DB that had previous head.
