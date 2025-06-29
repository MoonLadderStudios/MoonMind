#!/bin/sh

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
