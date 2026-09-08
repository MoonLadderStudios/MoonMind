#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create temporal databases and role
    CREATE USER ${TEMPORAL_POSTGRES_USER:-temporal} WITH ENCRYPTED PASSWORD '${TEMPORAL_POSTGRES_PASSWORD:-temporal}' CREATEDB;
    CREATE DATABASE ${TEMPORAL_POSTGRES_DB:-temporal} OWNER ${TEMPORAL_POSTGRES_USER:-temporal};
    CREATE DATABASE ${TEMPORAL_VISIBILITY_DB:-temporal_visibility} OWNER ${TEMPORAL_POSTGRES_USER:-temporal};
    GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_POSTGRES_DB:-temporal} TO ${TEMPORAL_POSTGRES_USER:-temporal};
    GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_VISIBILITY_DB:-temporal_visibility} TO ${TEMPORAL_POSTGRES_USER:-temporal};
    -- Bundled Keycloak database/role provisioning retired (MoonLadderStudios/MoonMind#4129).
    -- Shared PostgreSQL and Temporal databases above are preserved; existing
    -- databases are never dropped by initialization.
EOSQL
