# Alembic Migration Generation

**Process notes:** 

This document explains how to create new Alembic database migration scripts for this project. There are two approaches: using the API container (recommended) or using a local Poetry environment with `tools/generate_migrations.sh`.

## Approach 1: Using the API container (recommended)

This approach uses the API service image so you do not need a full local Python toolchain beyond Docker.

### Prerequisites

1. **Docker** running on your machine.
2. **Docker Compose** v2 (`docker compose`; the legacy `docker-compose` binary also works if installed).
3. A **`.env`** (or exported variables) consistent with `docker-compose.yaml` — notably `POSTGRES_PASSWORD` for the `postgres` service, which has no default in compose.

### Steps

1. **Project root**

   ```bash
   cd /path/to/MoonMind
   ```

2. **Start PostgreSQL**

   In `docker-compose.yaml` the database service is **`postgres`**. The API and Alembic use the hostname **`moonmind-api-db`** (a network alias on that service).

   ```bash
   docker compose up -d postgres
   ```

3. **Ensure the application image exists**

   The `api` service uses `image: ghcr.io/moonladderstudios/moonmind:latest` (no `build:` in the main compose file). Pull it:

   ```bash
   docker compose pull api
   ```

   To build an image locally instead, use `api_service/Dockerfile` and align the tag with what your compose file expects, or use a compose override — the important part is that the container includes Alembic and project dependencies.

4. **Generate the revision**

   ```bash
   docker compose run --rm api alembic -c /app/api_service/migrations/alembic.ini revision --autogenerate -m "your_migration_message"
   ```

   The `api` service mounts `./api_service:/app/api_service`, so new files under `api_service/migrations/versions/` appear on the host immediately.

5. **Verify**

   ```bash
   ls -la api_service/migrations/versions/
   ```

6. **Stop Postgres** (optional)

   ```bash
   docker compose stop postgres
   ```

### Alternative: plain `docker run`

Prefer Compose so networking and env match the repo. If you must use `docker run`, use the same **`POSTGRES_*`** variables as `moonmind.config.settings` (not `DATABASE_HOST`). Example pattern: build from `api_service/Dockerfile`, mount the repo at `/app`, set `POSTGRES_HOST` to a host or container IP where Postgres listens, then run the same `alembic -c /app/api_service/migrations/alembic.ini revision --autogenerate -m "..."` command.

## Approach 2: `tools/generate_migrations.sh` (Poetry + Docker)

Use this when you want a helper that can start a dedicated Postgres container for autogenerate.

### Prerequisites

1. **Docker** (the script can start `postgres_alembic_gen` if needed).
2. **Poetry** with project dependencies installed (`poetry install` at the repo root). The script invokes **`poetry run alembic`**, not a global `alembic` on `PATH`.
3. Run commands from the **repository root** (the script lives under `tools/` and resolves paths from there).

Alembic config and env live under `api_service/migrations/` (`alembic.ini`, `env.py`). `env.py` loads `AppSettings` and builds the sync URL from **`POSTGRES_HOST`**, **`POSTGRES_USER`**, **`POSTGRES_PASSWORD`**, **`POSTGRES_DB`**, **`POSTGRES_PORT`**.

### What the script does

- Checks for Docker.
- Uses or starts a container named **`postgres_alembic_gen`** (`postgres:15` by default in the script).
- Runs **`poetry run alembic -c <path-to-alembic.ini> upgrade head`**, then **`revision --autogenerate`** with your message.
- Optionally stops the container it started.

### Steps

```bash
cd /path/to/MoonMind
chmod +x tools/generate_migrations.sh   # Unix-like systems
./tools/generate_migrations.sh
```

When prompted, enter a short migration message (for example `add_user_profile_table`).

If the script starts Postgres, credentials are **`moonmind_user` / `moonmind_password`**, database **`moonmind_db`**, port **5432** on the host — see `tools/generate_migrations.sh` for the exact values.

## Important notes

- **Models**: Autogenerate depends on SQLAlchemy models in **`api_service/db/models.py`** and `target_metadata` in **`api_service/migrations/env.py`**. New models must be imported into the metadata graph Alembic sees.
- **Review**: Always review generated revisions; autogenerate is not infallible.
- **Applying migrations**: Generating a file does not apply it. In the default stack, the **`init-db`** one-shot service runs **`alembic ... upgrade head`** via `init_db/init_db_entrypoint.sh` after Postgres is up. The **`api_service/entrypoint.sh`** path does not run Alembic. You can also run `alembic upgrade head` manually in a container or with `poetry run alembic -c api_service/migrations/alembic.ini upgrade head` when your env points at the right database.

## Troubleshooting

### Container / Compose

- Confirm Docker is running and the project directory is shared correctly on Windows.
- Ensure **`POSTGRES_*`** in `.env` match what the `postgres` service and `api` / `init-db` containers use.
- If the application image is missing or outdated, `docker compose pull api` or rebuild from `api_service/Dockerfile`.

### Script / Poetry

- Port **5432** conflicts: stop other Postgres instances or change the script’s `DB_PORT`.
- **`Target database is not up to date`**: the script runs `upgrade head` first; if it still fails, connect to the intended DB and fix migration state.
- **`ModuleNotFoundError`**: use **`poetry run`** from the repo root so imports resolve; `PYTHONPATH` is set for containers via compose, not automatically for bare shells.

The Compose-based flow avoids most local dependency drift by using the same image the stack runs.
