# Generating Alembic Migrations

This document explains how to use the `generate_migrations.sh` script to create new Alembic database migration scripts for this project. This process typically requires a running PostgreSQL database for Alembic to compare your SQLAlchemy models against the current database schema.

## Prerequisites

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
    chmod +x docs/generate_migrations.sh
    ```

3.  **Run the Script**:
    ```bash
    ./docs/generate_migrations.sh
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

*   **`env.py` Configuration**: The Alembic environment (`api_service/migrations/env.py`) is currently configured to connect to a database at `postgresql+psycopg2://moonmind_user:moonmind_password@localhost:5432/moonmind_db`. The script ensures the Docker container matches this.
*   **Model Changes**: For Alembic to autogenerate changes, your SQLAlchemy models (likely in `api_service/db/models.py`) must be correctly imported and referenced by the `target_metadata` in `api_service/migrations/env.py`. Ensure all new or modified models are correctly set up.
*   **Review Generated Migrations**: Always review the generated migration script to ensure it accurately reflects the intended schema changes. Autogenerate is a powerful tool but not infallible.
*   **Applying Migrations**: This script *only generates* migration files. To apply them, you would typically run `alembic upgrade head` (from the `api_service/migrations` directory). The `prestart.sh` script in the `api_service` is configured to do this automatically when the API container starts.

## Troubleshooting

*   **Docker Errors**: Ensure Docker daemon is running. Check for port conflicts if `localhost:5432` is already in use by another service.
*   **Alembic Errors**:
    *   `Target database is not up to date`: This means there are existing migrations that haven't been applied to the database Alembic is connecting to. You might need to run `alembic upgrade head` first.
    *   `ModuleNotFoundError` or `ImportError`: Ensure all dependencies are installed in your Python environment and that `PYTHONPATH` is set up correctly if your project structure requires it for Alembic to find your models. The script assumes it's run from the project root, which should generally handle `PYTHONPATH` issues for imports like `api_service.db.models`.
    *   Connection errors: Verify credentials and database name in `env.py` and the Docker container setup.
*   **Script Permissions**: If you get a "Permission denied" error, ensure the script is executable (`chmod +x docs/generate_migrations.sh`).

This script provides a consistent way to generate migrations using a controlled database environment.
