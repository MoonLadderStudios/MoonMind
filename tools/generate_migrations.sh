#!/bin/bash
set -e

# --- Configuration ---
CONTAINER_NAME="postgres_alembic_gen"
DB_USER="moonmind_user"
DB_PASSWORD="moonmind_password"
DB_NAME="moonmind_db"
DB_PORT="5432"
POSTGRES_IMAGE="postgres:15" # Or your preferred Postgres version

# Determine Project Root and Alembic Directory dynamically
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT_DIR=$(dirname "$SCRIPT_DIR") # Assumes script is in a direct subdirectory of project root (e.g., tools/)
ALEMBIC_DIR="$PROJECT_ROOT_DIR/api_service/migrations"
ALEMBIC_CONFIG_FILE="$ALEMBIC_DIR/alembic.ini" # Used for running alembic command

# --- Helper Functions ---
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo "ERROR: Docker is not installed or not in PATH. Please install Docker to use this script."
        exit 1
    fi
}

is_container_running() {
    docker ps -f "name=${CONTAINER_NAME}" --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"
}

start_postgres_container() {
    echo "Starting PostgreSQL container '${CONTAINER_NAME}'..."
    docker run -d --name "${CONTAINER_NAME}" \
        -e POSTGRES_USER="${DB_USER}" \
        -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
        -e POSTGRES_DB="${DB_NAME}" \
        -p "${DB_PORT}:5432" \
        "${POSTGRES_IMAGE}"

    echo "Waiting for PostgreSQL to be ready..."
    # Simple wait loop, consider a more robust check (e.g., pg_isready) if needed for CI
    for i in {1..20}; do # Max wait 20 seconds
        if docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" -q; then
            echo "PostgreSQL is ready."
            return 0
        fi
        sleep 1
    done
    echo "ERROR: PostgreSQL container started but did not become ready in time."
    docker logs "${CONTAINER_NAME}"
    echo "You might need to stop and remove the container manually: docker stop ${CONTAINER_NAME} && docker rm ${CONTAINER_NAME}"
    return 1
}

stop_postgres_container() {
    echo "Stopping PostgreSQL container '${CONTAINER_NAME}'..."
    docker stop "${CONTAINER_NAME}" > /dev/null
    docker rm "${CONTAINER_NAME}" > /dev/null
    echo "Container '${CONTAINER_NAME}' stopped and removed."
}

# --- Main Script ---
check_docker

# The ALEMBIC_DIR is now absolute, so no need to check current path or cd from "docs"
# Check if Alembic target directory exists
if [ ! -d "$ALEMBIC_DIR" ]; then
    echo "ERROR: Alembic directory not found at ${ALEMBIC_DIR} (derived from script location)."
    exit 1
fi
if [ ! -f "$ALEMBIC_CONFIG_FILE" ]; then
    echo "ERROR: Alembic config file not found at ${ALEMBIC_CONFIG_FILE}."
    exit 1
fi

# Read migration message
read -r -p "Enter the migration message (e.g., 'create_initial_tables'): " MIGRATION_MESSAGE
if [ -z "$MIGRATION_MESSAGE" ]; then
    echo "ERROR: Migration message cannot be empty."
    exit 1
fi

# Manage PostgreSQL container
container_started_by_script=false
if is_container_running; then
    echo "PostgreSQL container '${CONTAINER_NAME}' is already running. Using it."
else
    echo "PostgreSQL container '${CONTAINER_NAME}' is not running."
    read -r -p "Do you want to start it now? (y/N): " confirm_start
    if [[ "$confirm_start" =~ ^[Yy]$ ]]; then
        if ! start_postgres_container; then
            exit 1 # Exit if container fails to start
        fi
        container_started_by_script=true
    else
        echo "Aborted. A running PostgreSQL container named '${CONTAINER_NAME}' (or one accessible at localhost:5432 with correct credentials) is required."
        exit 1
    fi
fi

# Generate migration
echo "Attempting to generate Alembic revision in ${ALEMBIC_DIR} using config ${ALEMBIC_CONFIG_FILE}..."
# Run alembic command from project root, but pass the config file path
# Alembic needs to run in a context where it can import the models, typically the project root or a directory in PYTHONPATH.
# The env.py in migrations dir usually handles path to models.
# The -c flag tells alembic where to find alembic.ini
# The actual CWD for alembic command itself might matter for how it resolves `script_location` in alembic.ini
# Let's try running it from project root, with absolute path to config.
# env.py typically uses `os.path.join(os.path.dirname(__file__), "..", "..")` to get to project root for model imports.

if alembic -c "${ALEMBIC_CONFIG_FILE}" revision --autogenerate -m "$MIGRATION_MESSAGE"; then
    echo "Alembic revision generated successfully in ${ALEMBIC_DIR}/versions."
else
    echo "ERROR: Alembic revision command failed. CWD is $(pwd)."
    # Optionally, stop the container if script started it
    if [ "$container_started_by_script" = true ]; then
        read -r -p "Alembic command failed. Stop the PostgreSQL container '${CONTAINER_NAME}'? (y/N): " confirm_stop_on_fail
        if [[ "$confirm_stop_on_fail" =~ ^[Yy]$ ]]; then
            stop_postgres_container
        fi
    fi
    # No cd was performed for alembic command, so no need to cd back
    exit 1
fi
# No cd was performed for alembic command, so no need to cd back

# Stop container if started by script
if [ "$container_started_by_script" = true ]; then
    read -r -p "Do you want to stop the PostgreSQL container '${CONTAINER_NAME}' now? (y/N): " confirm_stop
    if [[ "$confirm_stop" =~ ^[Yy]$ ]]; then
        stop_postgres_container
    fi
fi

echo "Script finished."
