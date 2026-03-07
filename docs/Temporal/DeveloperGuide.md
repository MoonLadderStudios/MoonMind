# Local Temporal Developer Guide

This guide covers how to bring up the full MoonMind Temporal execution stack on your local machine, how to configure `.env` correctly, and how to verify that the system is ready for testing and daily development.

## 1. Prerequisites

Before starting the Temporal stack, ensure you have:
- Docker and Docker Compose (V2+)
- A copy of `.env-template` saved as `.env` at the repository root.

## 2. Default Configuration

Your `.env` file should include the required Temporal configurations. If you are starting fresh, these defaults are already supplied in `.env-template`. Ensure they are present in your `.env` file:

```env
TEMPORAL_NAMESPACE="moonmind"
TEMPORAL_ADDRESS="temporal:7233"
TEMPORAL_UI_HOST_PORT=8088

# Tasks Routing
TEMPORAL_WORKFLOW_TASK_QUEUE="mm.workflow"
TEMPORAL_ACTIVITY_ARTIFACTS_TASK_QUEUE="mm.activity.artifacts"
TEMPORAL_ACTIVITY_LLM_TASK_QUEUE="mm.activity.llm"
TEMPORAL_ACTIVITY_SANDBOX_TASK_QUEUE="mm.activity.sandbox"
TEMPORAL_ACTIVITY_INTEGRATIONS_TASK_QUEUE="mm.activity.integrations"

# S3 Artifacts
TEMPORAL_ARTIFACT_BACKEND="s3"
TEMPORAL_ARTIFACT_S3_ENDPOINT="http://minio:9000"
TEMPORAL_ARTIFACT_S3_BUCKET="moonmind-temporal-artifacts"
TEMPORAL_ARTIFACT_S3_ACCESS_KEY_ID="minioadmin"
TEMPORAL_ARTIFACT_S3_SECRET_ACCESS_KEY="minioadmin"
TEMPORAL_ARTIFACT_S3_REGION="us-east-1"
TEMPORAL_ARTIFACT_S3_USE_SSL="false"
```

*Note: Ensure that `TEMPORAL_WORKER_COMMAND` is **not** set to `sleep infinity` in your `.env` file. It should either be unset or explicitly empty so that the workers launch their respective pollers automatically.*

## 3. Starting the Environment

To start the required Temporal services, worker fleets, and supporting infrastructure (API, DB, MinIO), run the following command from the repository root:

```bash
docker compose up -d minio temporal-db temporal temporal-namespace-init \
  temporal-worker-workflow temporal-worker-artifacts \
  temporal-worker-llm temporal-worker-sandbox temporal-worker-integrations \
  api
```

This will launch:
1. `minio`: The S3-compatible backend for execution artifacts.
2. `temporal-db` & `temporal`: The core Temporal execution engine and storage.
3. `temporal-namespace-init`: A script container that idempotently creates the `moonmind` namespace and exits successfully.
4. `temporal-worker-*`: Five specialized Temporal workers that poll their respective task queues and process activities/workflows.
5. `api`: The core MoonMind API backend, including `/api/executions`.

### Optional: Starting the Temporal UI

If you want to view workflow executions in your browser via the Temporal UI, you can launch it using the `temporal-ui` profile:

```bash
docker compose --profile temporal-ui up -d temporal-ui
```

The UI will be accessible at `http://localhost:8088` (or the port defined by `TEMPORAL_UI_HOST_PORT`).

## 4. Verifying Health & Readiness

After running `docker compose up`, verify that the environment has reached a healthy, usable state.

### 4.1. Namespace Bootstrap

The `temporal-namespace-init` container should exit with code 0 once the namespace is created. You can verify this by checking its logs:

```bash
docker compose logs temporal-namespace-init
```

**Expected output:**
```
Namespace moonmind already exists
# OR
Namespace moonmind successfully registered.
```

### 4.2. Worker Pollers

The five temporal workers should actively connect to Temporal Server and log that they are polling their respective queues. Check the logs for `temporal-worker-workflow` as an example:

```bash
docker compose logs -f temporal-worker-workflow
```

**Expected output:**
You should see output similar to the following, confirming that the worker is running and ready for tasks:
```
INFO:temporalio.worker:Worker started
INFO:moonmind.workflows.temporal.worker_runtime:Worker workflow polling task queue mm.workflow
```

### 4.3. MinIO Reachability

Verify that MinIO is healthy and running on port `9000` (API) and `9001` (Console):

```bash
docker compose logs minio
```

The logs should indicate that the MinIO server has started and initialized successfully.

### 4.4. API Readiness

Ensure the MoonMind API is ready and capable of receiving execution requests.

```bash
curl -f http://localhost:5000/health
```

**Expected output:**
```json
{"status":"ok"}
```

## 5. End-to-End Execution Creation

To prove that the stack is functioning correctly without manually executing commands inside a container, trigger a Temporal-backed task creation via the `/api/executions` endpoint.

```bash
curl -X POST http://localhost:5000/api/executions \
  -H "Content-Type: application/json" \
  -d '{
    "entry": "Test execution",
    "inputs": {}
  }'
```

**Expected Result:**
You should receive a JSON response containing the newly created execution details, including a valid `workflowId`. You can verify that this execution shows up in the Temporal UI (if enabled) at `http://localhost:8088`.

## 6. Troubleshooting Guidance

### Workers remain idle / stuck

If you run `docker compose logs temporal-worker-workflow` and see nothing, or if the container seems paused:
- Ensure `TEMPORAL_WORKER_COMMAND` is not set to `sleep infinity` in your `.env` file.
- Check that `temporal-namespace-init` successfully created the namespace. Workers cannot start until the namespace exists.

### "Context deadline exceeded" / Connection issues

If workers or the API are throwing connection timeout errors trying to reach Temporal:
- Verify that `temporal-db` and `temporal` are both running and healthy (`docker compose ps`).
- Check `temporal` logs for database migration issues or bootup panics.

### Workflow errors writing artifacts

If a workflow fails immediately with an error relating to `boto3` or S3:
- Ensure the `minio` service is running.
- Verify that the `moonmind-temporal-artifacts` bucket exists. The API or worker lifecycle logic should create it upon startup if it is missing, but if not, you can manually create it via the MinIO console at `http://localhost:9001`.
