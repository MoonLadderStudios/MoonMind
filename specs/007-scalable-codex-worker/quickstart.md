# Quickstart: Scalable Codex Worker

**Feature**: Scalable Codex Worker (007)
**Date**: 2025-11-27

## Overview

The Scalable Codex Worker is a dedicated Celery worker for processing Codex-related automation tasks. It requires a one-time manual authentication setup to persist credentials in a shared Docker volume.

## Prerequisites

- Docker and Docker Compose installed.
- MoonMind repository cloned.
- Valid Codex account credentials.

## Setup Instructions

### 1. Authenticate the Volume

Before starting the worker, you must populate the `codex_auth_volume` with valid credentials.

1.  **Start a temporary interactive shell** with the volume mounted:
    ```bash
    docker compose run --rm celery_codex_worker /bin/bash
    ```

2.  **Run the login command** inside the container:
    ```bash
    codex login
    # Follow the on-screen instructions to authenticate via browser.
    ```

3.  **Verify authentication**:
    ```bash
    codex whoami
    # Should return your user details.
    ```

4.  **Configure non-interactive mode** (if not default):
    ```bash
    # Ensure config.toml exists and set approval policy
    mkdir -p ~/.codex
    echo 'approval_policy = "never"' >> ~/.codex/config.toml
    ```

5.  **Exit the container**:
    ```bash
    exit
    ```

### 2. Start the Worker

Once authenticated, start the worker service normally.

```bash
docker compose up -d celery_codex_worker
```

### 3. Verification

Check the logs to ensure the worker started and passed the pre-flight check.

```bash
docker compose logs -f celery_codex_worker
```

**Success Output:**
```text
...
Successfully authenticated as [User]
[2025-11-27 10:00:00,000: INFO/MainProcess] Connected to redis://...
[2025-11-27 10:00:00,000: INFO/MainProcess] celery@... ready.
```

**Failure Output (Unauthenticated):**
```text
...
Error: Not authenticated. Please run 'codex login'.
...
exited with code 1
```

## Scaling

To scale the worker to multiple replicas:

```bash
docker compose up -d --scale celery_codex_worker=3
```
