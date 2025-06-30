# Generating Alembic Migrations

This document explains how to create new Alembic database migration scripts for this project. There are two approaches: using the API container (recommended) or using a local Python environment with the `generate_migrations.sh` script.

## Approach 1: Using the API Container (Recommended)

This approach uses the API service container that already has all Python dependencies installed, eliminating the need to set up a local Python environment.

### Prerequisites

1.  **Docker**: You must have Docker installed and running on your system.
2.  **Docker Compose**: Ensure `docker-compose` is available.

### Steps to Generate Migrations Using the API Container

1.  **Navigate to Project Root**:
    Open your terminal and change to the root directory of the project.
    ```bash
    cd /path/to/MoonMind
    ```

2.  **Start the PostgreSQL Database**:
    The API container needs a PostgreSQL database to compare against. Start just the PostgreSQL service (now named `api-db`):
    ```bash
    docker-compose up -d api-db
    ```

3.  **Build the API Image** (if not already built):
    ```bash
    docker-compose build api
    ```

4.  **Generate the Migration**:
    Run Alembic inside the API container to generate the migration. Replace `"your_migration_message"` with a descriptive message:
    ```bash
    docker-compose run --rm api alembic -c /app/api_service/migrations/alembic.ini revision --autogenerate -m "initial_migration"
    ```

5.  **Verify the Migration**:
    The new migration file will be created in `api_service/migrations/versions/`. You can list the files to see the newly generated migration:
    ```bash
    ls -la api_service/migrations/versions/
    ```

6.  **Stop the Database** (optional):
    If you don't need the database running, you can stop it:
    ```bash
    docker-compose down api-db
    ```

### Alternative: Using Docker Run Command

If you prefer not to use docker-compose, you can use the docker run command directly:

1.  **Start PostgreSQL Container**:
    ```bash
    docker run -d --name moonmind_postgres \
        -e POSTGRES_USER=postgres \
        -e POSTGRES_PASSWORD=password \
        -e POSTGRES_DB=mydatabase \
        -p 5432:5432 \
        pgvector/pgvector:pg16
    ```

2.  **Build and Run Migration**:
    ```bash
    # Build the API image
    docker build -t moonmind-api -f api_service/Dockerfile .

    # Generate migration
    docker run --rm \
        --network host \
        -v "$(pwd)":/app \
        -w /app \
        -e POSTGRES_USER=postgres \
        -e POSTGRES_PASSWORD=password \
        -e POSTGRES_DB=mydatabase \
        -e DATABASE_HOST=localhost \
        moonmind-api \
        alembic -c /app/api_service/migrations/alembic.ini revision --autogenerate -m "your_migration_message"
    ```

3.  **Clean Up**:
    ```bash
    docker stop moonmind_postgres && docker rm moonmind_postgres
    ```

## Approach 2: Using Local Python Environment with Script

This is the traditional approach that requires a local Python environment.

### Prerequisites

1.  **Docker**: You must have Docker installed and running on your system. The script uses Docker to manage a PostgreSQL instance if one isn't already available.
2.  **Python Environment**: Ensure you have a Python environment set up with all the project dependencies installed, including `alembic`, `psycopg2-binary` (or `psycopg2`), `SQLAlchemy`, and `SQLAlchemy-Utils`. This is usually done by installing from a `requirements.txt` file:
    ```bash
    pip install -r requirements.txt
    # Or specific to the api_service if applicable
    # pip install -r api_service/requirements.txt
    ```
    Ensure `alembic` is in your PATH.
3.  **Project Structure**: The script assumes it is located in the `docs/` directory and you run it from the project root, or it will attempt to navigate to the project root. The Alembic configuration (`alembic.ini` and `env.py`) is expected to be in `api_service/migrations/`.

## Using the `generate_migrations.sh` Script

The `generate_migrations.sh` script automates several steps:
    - Checking for Docker.
    - Ensuring a PostgreSQL database is available (either by using an existing container named `postgres_alembic_gen` or by starting a new one).
    - Running the `alembic revision --autogenerate` command.
    - Optionally stopping the PostgreSQL container if the script started it.

### Steps to Run:

1.  **Navigate to Project Root**:
    Open your terminal and change to the root directory of the project.
    ```bash
    cd /path/to/your/project
    ```

2.  **Make the Script Executable** (if you haven't already):
    ```bash
    chmod +x tools/generate_migrations.sh
    ```

3.  **Run the Script**:
    ```bash
    ./tools/generate_migrations.sh
    ```

4.  **Enter Migration Message**:
    The script will prompt you to enter a migration message. This message is used as part of the generated migration filename and for descriptive purposes (e.g., "add_user_profile_table", "update_indexes_on_user").
    ```
    Enter the migration message (e.g., 'create_initial_tables'): <your_message_here>
    ```

5.  **PostgreSQL Container Management**:
    *   **Existing Container**: If a Docker container named `postgres_alembic_gen` is already running, the script will offer to use it.
    *   **New Container**: If no such container is running, the script will ask if you want to start one. If you agree:
        *   It will start a `postgres:15` (or as configured in the script) container named `postgres_alembic_gen`.
        *   The database will be configured with:
            *   User: `moonmind_user`
            *   Password: `moonmind_password`
            *   Database: `moonmind_db`
            *   Port: `5432` (mapped to host port `5432`)
        *   The script waits for the database to become ready.
    *   **Refusing Start**: If you refuse to start a new container and one isn't running, the script will abort.

6.  **Alembic Revision Generation**:
    The script then navigates to `api_service/migrations/` and runs:
    ```bash
    alembic revision --autogenerate -m "your_message_here"
    ```
    If successful, a new migration file will be created in `api_service/migrations/versions/`.

7.  **Stopping the Container**:
    If the script started the PostgreSQL container, it will ask if you want to stop and remove it after the migration generation (or if it fails).

## Important Notes

*   **Database Configuration**: The API container is configured to connect to the PostgreSQL database using environment variables defined in your `.env` file or docker-compose configuration.
*   **Model Changes**: For Alembic to autogenerate changes, your SQLAlchemy models (likely in `api_service/db/models.py`) must be correctly imported and referenced by the `target_metadata` in `api_service/migrations/env.py`. Ensure all new or modified models are correctly set up.
*   **Review Generated Migrations**: Always review the generated migration script to ensure it accurately reflects the intended schema changes. Autogenerate is a powerful tool but not infallible.
*   **Volume Mounting**: When using the container approach, the project directory is mounted into the container, so the generated migration files will appear in your local filesystem.
*   **Applying Migrations**: These approaches *only generate* migration files. To apply them, you would typically run `alembic upgrade head` (either in the container or locally). The `prestart.sh` script in the `api_service` is configured to do this automatically when the API container starts in the full docker-compose environment.

## Troubleshooting

### Container Approach Issues
*   **Docker Errors**: Ensure Docker daemon is running.
*   **Permission Issues**: On Windows, ensure Docker has access to the project directory.
*   **Database Connection**: If using custom environment variables, ensure they match between the API container and PostgreSQL container.
*   **Image Build Issues**: If the API image fails to build, check that all required files (pyproject.toml, README.md, etc.) are present.

### Script Approach Issues
*   **Docker Errors**: Ensure Docker daemon is running. Check for port conflicts if `localhost:5432` is already in use by another service.
*   **Alembic Errors**:
    *   `Target database is not up to date`: This means there are existing migrations that haven't been applied to the database Alembic is connecting to. You might need to run `alembic upgrade head` first.
    *   `ModuleNotFoundError` or `ImportError`: Ensure all dependencies are installed in your Python environment and that `PYTHONPATH` is set up correctly if your project structure requires it for Alembic to find your models. The script assumes it's run from the project root, which should generally handle `PYTHONPATH` issues for imports like `api_service.db.models`.
    *   Connection errors: Verify credentials and database name in `env.py` and the Docker container setup.
*   **Script Permissions**: If you get a "Permission denied" error, ensure the script is executable (`chmod +x tools/generate_migrations.sh`).

The container approach eliminates most Python environment and dependency issues by using a pre-configured environment with all necessary packages installed.
