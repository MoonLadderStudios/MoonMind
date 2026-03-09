# Postgres Consolidation Plan

This document outlines the architectural changes required to consolidate the currently separate PostgreSQL containers (`api-db`, `temporal-db`, `keycloak-db`) into a single unified `postgres` container in `docker-compose.yaml` and the necessary updates for all relying services.

## Overview of Current State
Currently, `docker-compose.yaml` defines three separate PostgreSQL instances:
- `api-db`: DB `moonmind` for the core API service.
- `temporal-db`: DB `temporal` for Temporal server backend.
- `keycloak-db`: DB `keycloak` for Keycloak identity provider.

## Target Architecture
A single PostgreSQL instance (named `postgres` or `shared-db`) will host multiple logical databases (`moonmind`, `temporal`, `keycloak`, and ideally `temporal_visibility`). This reduces memory footprint, container overhead, and simplifies volume management.

## Detailed Migration Steps

### 1. Update `docker-compose.yaml` Global Configuration

#### Add Consolidated Postgres Service
Create a new service block for the unified Postgres instance. We will mount an initialization directory to create the multiple required databases and roles since the official Postgres image only creates one database out-of-the-box via the `POSTGRES_DB` variable.

```yaml
  postgres:
    image: postgres:${POSTGRES_VERSION:-17}
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-password}
      POSTGRES_DB: ${POSTGRES_DB:-moonmind}
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./init_db_scripts:/docker-entrypoint-initdb.d
    expose:
      - "5432"
    networks:
      local-network:
        aliases:
          - moonmind-api-db
      temporal-network:
        aliases:
          - temporal-db
    restart: unless-stopped
    healthcheck:
      test:
        - CMD-SHELL
        - pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-moonmind}
      interval: 10s
      timeout: 5s
      retries: 5
```

*Note on Network Aliases:* By preserving the `moonmind-api-db` and `temporal-db` aliases on the new container, we significantly reduce the necessary updates to dependent configurations.

#### Remove Old Postgres Services
Remove the following service blocks from `docker-compose.yaml`:
- `api-db`
- `temporal-db`
- `keycloak-db`

#### Update Volumes
In the `volumes:` section at the bottom of `docker-compose.yaml`:
```yaml
volumes:
  postgres-data: # Add unified volume
  # Remove old volumes:
  # - api-db-data:
  # - temporal-db-data:
  # - keycloak-db-data:
```

### 2. Database Initialization Script
Create an initialization SQL script that will run automatically when the new `postgres` container is created. The official image executes `*.sql` files located in `/docker-entrypoint-initdb.d/`.

Create `./init_db_scripts/01-create-dbs.sh`:
```sh
#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create temporal databases and role
    CREATE USER ${TEMPORAL_POSTGRES_USER:-temporal} WITH ENCRYPTED PASSWORD '${TEMPORAL_POSTGRES_PASSWORD:-temporal}';
    CREATE DATABASE ${TEMPORAL_POSTGRES_DB:-temporal};
    CREATE DATABASE ${TEMPORAL_VISIBILITY_DB:-temporal_visibility};
    GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_POSTGRES_DB:-temporal} TO ${TEMPORAL_POSTGRES_USER:-temporal};
    GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_VISIBILITY_DB:-temporal_visibility} TO ${TEMPORAL_POSTGRES_USER:-temporal};

    -- Create keycloak database and role
    CREATE USER keycloak WITH ENCRYPTED PASSWORD '${KC_DB_PW:-keycloak}';
    CREATE DATABASE keycloak;
    GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
EOSQL
```

### 3. Update Dependent Services

Identify and update all services that depended on the old database containers. They must now define their `depends_on` the new `postgres` service constraint.

#### A. Keycloak Service
- **Change Dependencies**: Change `depends_on` from `keycloak-db` to `postgres`.
- **Change DB URL**: Ensure `--db-url` still resolves correctly. If we don't alias `keycloak-db` in the `local-network` blocks, we must update the connection string.
```yaml
  keycloak:
    ...
    command: >
      start-dev
      ...
      --db-url=jdbc:postgresql://postgres:5432/keycloak
    depends_on:
      postgres:
        condition: service_healthy
```

#### B. API Core Services (`api`, `init-db`, `scheduler`, `codex-worker`, `gemini-worker`, `claude-worker`, `orchestrator`)
- **Change Dependencies**: Update `depends_on` from `api-db` to `postgres`.
- **Change Database References**: 
  - `POSTGRES_HOST` can remain `moonmind-api-db` because of our network alias.
```yaml
  api:
    depends_on:
      postgres:
        condition: service_healthy
      # init-db:
      #   condition: service_completed_successfully
```
*Apply `depends_on: postgres` to `init-db` and `scheduler` respectively.*

#### C. Temporal Services (`temporal`, `temporal-visibility-rehearsal`)
- **Change Dependencies**: Update `depends_on` from `temporal-db` to `postgres`.
- **Change DB References**:
  - `POSTGRES_SEEDS` can remain `temporal-db` if we properly aliased the new postgres container in the `temporal-network`. Otherwise, change it to `postgres`.
```yaml
  temporal:
    depends_on:
      postgres:
        condition: service_healthy
```
Make sure `temporal-visibility-rehearsal` is also updated:
```yaml
  temporal-visibility-rehearsal:
    environment:
      - TEMPORAL_POSTGRES_HOST=postgres
    depends_on:
      postgres:
        condition: service_healthy
```

### 4. Data Migration
Because this architectural change transitions from separate independent volumes (`api-db-data`, `temporal-db-data`, `keycloak-db-data`) to a single `postgres-data` volume, existing local container data will not carry over automatically.

1. **Local Development Environments**:
   Developers can usually wipe their local Docker volumes. Standard initialization via `docker compose up -d` will trigger the new initialization script and provision fresh databases.
2. **Data Preservation**:
   If specific database data needs preservation:
   - Perform `pg_dump` on the respective old containers.
   - Boot up the new consolidated `postgres` container.
   - Run `pg_restore` for each respective database to import the data back.
3. **Environment File Defaults**:
   Verify `.env` and `.env-template` files don't hardcode unexpected overrides for Keycloak passwords or Postgres credentials that conflict with the initialization scripts.
