#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create temporal databases and role
    CREATE USER ${TEMPORAL_POSTGRES_USER:-temporal} WITH ENCRYPTED PASSWORD '${TEMPORAL_POSTGRES_PASSWORD:-temporal}' CREATEDB;
    CREATE DATABASE ${TEMPORAL_POSTGRES_DB:-temporal} OWNER ${TEMPORAL_POSTGRES_USER:-temporal};
    CREATE DATABASE ${TEMPORAL_VISIBILITY_DB:-temporal_visibility} OWNER ${TEMPORAL_POSTGRES_USER:-temporal};
    GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_POSTGRES_DB:-temporal} TO ${TEMPORAL_POSTGRES_USER:-temporal};
    GRANT ALL PRIVILEGES ON DATABASE ${TEMPORAL_VISIBILITY_DB:-temporal_visibility} TO ${TEMPORAL_POSTGRES_USER:-temporal};

    -- Create keycloak database and role
    CREATE USER keycloak WITH ENCRYPTED PASSWORD '${KC_DB_PW:-keycloak}';
    CREATE DATABASE keycloak;
    GRANT ALL PRIVILEGES ON DATABASE keycloak TO keycloak;
EOSQL
