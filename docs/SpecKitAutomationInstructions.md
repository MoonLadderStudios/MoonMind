# Spec Kit Automation Instructions

These instructions describe how to launch the Spec Kit automation pipeline so that, after you supply credentials, the desired repository, and the Spec Kit input text, the system handles cloning, running `/speckit.specify`, `/speckit.plan`, and `/speckit.tasks`, committing any changes, and opening a draft pull request automatically.【F:docs/SpecKitAutomation.md†L12-L78】【F:specs/002-document-speckit-automation/spec.md†L11-L45】

## Prerequisites

1. **Runtime services** – Bring up RabbitMQ, the Spec Kit Celery worker, and the API service together; they share the automation queue, result backend, and REST surface used to trigger and monitor runs.【F:docs/SpecKitAutomation.md†L32-L95】【F:specs/002-document-speckit-automation/quickstart.md†L47-L55】
2. **Credentials** – Export a GitHub token with `repo` scope and the Codex API key so the job container can clone, push, and execute Spec Kit prompts. Set `SPEC_WORKFLOW_TEST_MODE=true` when you only want to dry-run without pushing changes.【F:specs/002-document-speckit-automation/quickstart.md†L32-L43】【F:docs/SpecKitAutomation.md†L116-L129】
3. **Spec input** – Prepare the text (YAML/JSON/Markdown) you want to feed into `/speckit.specify`; save it locally so it can be injected into the run request body.【F:specs/002-document-speckit-automation/spec.md†L11-L45】
4. **API access** – Obtain an access token for the MoonMind API (e.g., via Keycloak) because `/api/spec-automation` endpoints require authentication. Export it as `MOONMIND_API_TOKEN` for convenience.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L101】

## Step 1 – Start the automation stack

From the repository root, start the supporting services in one terminal so the worker can accept automation jobs:

```bash
docker compose up rabbitmq celery-worker api
```

Leave the stack running. To launch job containers successfully, the Celery worker needs access to the host Docker socket and a shared `speckit_workspaces` volume that is also readable by the API service. The base `docker-compose.yaml` does not wire these mounts, so create an override file before starting the stack:

```bash
cat <<'EOF' > docker-compose.override.yaml
services:
  celery-worker:
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - speckit_workspaces:/work/runs
  api:
    volumes:
      - speckit_workspaces:/work/runs:ro
volumes:
  speckit_workspaces:
EOF
```

Docker Compose automatically loads `docker-compose.override.yaml`, ensuring each run gets an isolated workspace under `/work/runs/<run_id>` while the worker can orchestrate job containers.【F:docs/SpecKitAutomation.md†L32-L113】【F:specs/002-document-speckit-automation/quickstart.md†L47-L106】【F:moonmind/workflows/speckit_celery/workspace.py†L1-L48】

## Step 2 – Prepare your run request

In a second terminal, load your credentials and craft the JSON payload that specifies the feature key and any optional overrides (forced phase or `createdBy` audit metadata). The snippet below uses `jq` to safely assemble the request:

```bash
# Pull credentials from your preferred secret manager so they never land in
# shell history. The example below uses the 1Password CLI; substitute `pass`,
# `aws secretsmanager`, or another tool that fits your environment.
export GITHUB_TOKEN="$(op read 'op://moonmind/github-token/value')"
export CODEX_API_KEY="$(op read 'op://moonmind/codex-api-key/value')"
export MOONMIND_API_TOKEN="$(op read 'op://moonmind/moonmind-api-token/value')"

export SPEC_AUTOMATION_FEATURE_KEY="002-document-speckit-automation"
export SPEC_AUTOMATION_FORCE_PHASE=""          # optional: discover|submit|apply|publish
export SPEC_AUTOMATION_CREATED_BY=""           # optional: UUID for audit trails

REQUEST_BODY=$(jq -n \
  --arg feature "$SPEC_AUTOMATION_FEATURE_KEY" \
  --arg force "$SPEC_AUTOMATION_FORCE_PHASE" \
  --arg created "$SPEC_AUTOMATION_CREATED_BY" \
  '{"featureKey": $feature, "forcePhase": ($force // empty), "createdBy": ($created // empty)}
    | with_entries(select(.value != ""))'
)
```

If you cannot rely on a secrets manager, temporarily disable shell history
(`set +o history`) before exporting the variables, and remember to `unset` them
once the run finishes.

## Step 3 – Trigger the automation run

Submit the payload to the workflow orchestration API. The endpoint enqueues the Celery chain and returns the workflow run metadata, including the `id` you will use in subsequent monitoring calls:

```bash
printf '%s' "$REQUEST_BODY" | \
curl -sS -X POST "http://localhost:5000/api/workflows/speckit/runs" \
  -H "Authorization: Bearer ${MOONMIND_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d @-
```

> **Note:** The default `docker-compose.yaml` maps the FastAPI service to
> `localhost:5000`. If you change the Compose file or run the service directly,
> adjust the host and port accordingly.

The response conforms to `CreateWorkflowRunRequest`/`SpecWorkflowRunModel` and includes fields such as `status`, `featureKey`, and `celeryChainId` to confirm that the run was accepted.【F:moonmind/schemas/workflow_models.py†L73-L148】【F:api_service/api/routers/workflows.py†L38-L87】

A successful response includes the queued status and `id` (the workflow run identifier). Behind the scenes the worker allocates a workspace, starts the job container, clones the repository, and executes the Spec Kit phases in order before committing and pushing changes when a diff exists.【F:docs/SpecKitAutomation.md†L66-L112】【F:moonmind/workflows/speckit_celery/orchestrator.py†L124-L150】

Ensure the Celery worker (or Compose stack) exports `SPEC_WORKFLOW_GITHUB_REPOSITORY` and related overrides before you trigger a run so the orchestrator can clone the correct repository and configure the agent clients.【F:moonmind/config/settings.py†L138-L190】【F:moonmind/workflows/speckit_celery/tasks.py†L568-L585】

The queued run is now ready for monitoring.

## Step 4 – Monitor progress

1. **Worker logs** – Tail the Celery worker to see phase transitions and retry activity:
   ```bash
   docker compose logs -f celery-worker
   ```
2. **Status API** – Poll run state (phases, branch, PR URL, artifacts) using the run identifier returned earlier:
   ```bash
   curl -H "Authorization: Bearer ${MOONMIND_API_TOKEN}" \
     "http://localhost:5000/api/spec-automation/runs/<run_id>"
   ```
3. **Artifacts** – Inspect logs and generated assets under `/work/runs/<run_id>/artifacts` or download them through `/api/spec-automation/runs/<run_id>/artifacts/<artifact_id>` as needed.【F:docs/SpecKitAutomation.md†L131-L156】【F:specs/002-document-speckit-automation/quickstart.md†L82-L106】【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L32-L77】

## Step 5 – Review results and handoff

When the run reports `succeeded` (or `no_changes`), use the status response to capture:

- `branch_name` and `pull_request_url` for reviewer handoff.
- Artifact IDs for stdout/stderr, diff summaries, credential audits, and commit status logs.

Because each run uses an isolated workspace and redacts sensitive environment values, you can safely share artifacts with reviewers while leaving the Celery stack running for additional requests.【F:docs/SpecKitAutomation.md†L100-L156】

## Step 6 – Cleanup (optional)

To shut down the environment, stop the Compose stack and prune cached workspaces once all artifacts have been collected:

```bash
docker compose down --volumes
```

This removes the worker containers and deletes cached workspaces so future automation runs start from a clean slate.【F:docs/SpecKitAutomation.md†L94-L113】【F:specs/002-document-speckit-automation/quickstart.md†L109-L118】
