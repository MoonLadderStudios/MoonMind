# Quickstart: Gemini CLI Worker

**Feature**: Gemini CLI Worker
**Branch**: `008-gemini-cli-worker`

## Prerequisites

- Docker and Docker Compose installed.
- A valid Gemini API Key (`GEMINI_API_KEY`).

## Setup

1.  **Configure Environment**:
    Add your API key to `.env`:
    ```bash
    GEMINI_API_KEY=your_api_key_here
    ```

2.  **Start the Worker**:
    ```bash
    docker compose up -d celery_gemini_worker
    ```

3.  **Verify Startup**:
    Check logs to ensure connection to the `gemini` queue:
    ```bash
    docker compose logs -f celery_gemini_worker
    ```
    Look for: `[config] . . . . > gemini` in the queue list.

## Testing

1.  **Submit a Test Task** (via shell):
    You can use the python shell in the `api` container to enqueue a task:
    ```bash
    docker compose exec api python
    ```
    ```python
    from celery_worker.speckit_worker import celery_app
    # Assuming task is registered
    celery_app.send_task('gemini_worker.tasks.generate', args=['Hello Gemini'], queue='gemini')
    ```

2.  **Check Worker Logs**:
    Verify the worker picked up the task and executed the CLI command.
