#!/bin/sh

python - <<'PY'
import os

from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])

with engine.connect() as connection:
    version = connection.execute(
        text("select version_num from alembic_version limit 1")
    ).scalar()
    has_projection_version = connection.execute(
        text(
            """
            select exists (
                select 1
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'temporal_executions'
                  and column_name = 'projection_version'
            )
            """
        )
    ).scalar()

if version == "202603060001" and not has_projection_version:
    raise SystemExit(42)
PY
status=$?
if [ $status -eq 42 ]; then
    echo 'Detected legacy duplicated Alembic revision stamp 202603060001; restamping to 202603050002 before upgrade.';
    alembic -c /app/api_service/migrations/alembic.ini stamp 202603050002;
    if [ $? -ne 0 ]; then
        echo 'Alembic restamp failed';
        exit 1;
    fi;
elif [ $status -ne 0 ]; then
    echo 'Alembic preflight failed';
    exit $status;
fi;

echo 'Running Alembic migrations...';
alembic -c /app/api_service/migrations/alembic.ini upgrade head;
if [ $? -ne 0 ]; then
    echo 'Alembic migration failed';
    exit 1;
fi;
if [ \"$INIT_DATABASE\" = \"true\" ]; then
    echo 'Attempting to initialize vector database...';
    python /app/init_db/init_vector_db.py;
else
    echo 'INIT_DATABASE not set to true, skipping vector DB initialization.';
fi
