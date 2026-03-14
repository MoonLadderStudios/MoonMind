# Spec Kit Automation Instructions

These instructions describe how to launch the Spec Kit automation pipeline so that, after you supply credentials, the desired repository, and the Spec Kit input text, the system handles cloning, running `/agentkit.specify`, `/agentkit.plan`, and `/agentkit.tasks`, committing any changes, and opening a pull request automatically.【F:docs/AgentKitAutomation.md†L12-L78】【F:specs/002-document-agentkit-automation/spec.md†L11-L45】

## Prerequisites

1. **Runtime services** – Bring up RabbitMQ, the Codex-focused Celery worker, and the API service together; they share the automation queues, result backend, and REST surface used to trigger and monitor runs.【F:docs/AgentKitAutomation.md†L32-L95】【F:specs/002-document-agentkit-automation/quickstart.md†L47-L55】
   - Bind the worker to both Workflow queues (`${CELERY_DEFAULT_QUEUE:-agentkit}` and `${WORKFLOW_CODEX_QUEUE:-codex}`), so discovery and Codex phases execute on the same worker process.
2. **Credentials** – Export a GitHub token with `repo` scope, ensure `CODEX_ENV` and `CODEX_MODEL` are configured, and authenticate the Codex auth volume (`./tools/auth-codex-volume.sh`, which runs `codex login --device-auth`) so the pre-flight status check passes before jobs start. Set `WORKFLOW_TEST_MODE=true` when you only want to dry-run without pushing changes.【F:specs/002-document-agentkit-automation/quickstart.md†L32-L43】【F:docs/AgentKitAutomation.md†L116-L129】
3. **Spec input** – Prepare the text (YAML/JSON/Markdown) you want to feed into `/agentkit.specify`; save it locally so it can be injected into the run request body.【F:specs/002-document-agentkit-automation/spec.md†L11-L45】
4. **API access** – Obtain an access token for the MoonMind API (e.g., via Keycloak) because `/api/workflows` endpoints require authentication.【F:specs/002-document-agentkit-automation/contracts/workflow.openapi.yaml†L11-L101】 If you are using the bundled Keycloak realm, walk through the steps below and then export the resulting bearer token as `MOONMIND_API_TOKEN`.
    1. **Start Keycloak** – Run `docker compose --profile keycloak up keycloak keycloak-db -d` so the `moonmind` realm is available at `http://localhost:8085`. The default admin credentials are `admin/admin` unless you set `KC_ADMIN_PW`.
    2. **Point the API at Keycloak** – In your `.env`, set `AUTH_PROVIDER=keycloak`, `OIDC_ISSUER_URL=http://localhost:8085/realms/moonmind`, `OIDC_CLIENT_ID=api-service`, and `OIDC_CLIENT_SECRET=${API_CLIENT_SECRET:-changeme}` (match any overrides you applied in `docker-compose.yaml`). Restart the `api` container so it reloads the settings.
    3. **Create a user** – Sign in to the Keycloak admin console, select the `moonmind` realm, and add (or reset) a user that will own automation runs. Under the **Credentials** tab, set a non-temporary password and note the username/password pair for the next step.
    4. **Exchange credentials for a token** – Request an access token from the OpenID Connect token endpoint. For example:

        ```bash
        export OIDC_BASE_URL="http://localhost:8085/realms/moonmind/protocol/openid-connect/token"
        export OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-api-service}"
        export OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET:-changeme}"
        export MOONMIND_USERNAME="<keycloak-username>"
        export MOONMIND_PASSWORD="<keycloak-password>"

        TOKEN_RESPONSE="$(
          curl -sS -X POST "$OIDC_BASE_URL" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "grant_type=password" \
            -d "client_id=${OIDC_CLIENT_ID}" \
            -d "client_secret=${OIDC_CLIENT_SECRET}" \
            -d "username=${MOONMIND_USERNAME}" \
            -d "password=${MOONMIND_PASSWORD}" \
            -d "scope=openid"
        )"
        export MOONMIND_API_TOKEN="$(printf '%s' "$TOKEN_RESPONSE" | jq -r '.access_token')"
        ```

        The token expires based on the Keycloak realm settings (30 minutes by default). Re-run the command whenever the token lapses, or capture the refresh token from the same response if you want to automate renewal.

### Codex & Spec Kit CLI environment variables

To keep Codex and Spec Kit CLI installs deterministic, standardize on two environment variables that will be consumed by the Docker build arguments introduced in this spec. Export them (or pass them inline) before invoking `docker build` so the Node-based tooling builder can resolve the correct packages as the remaining tasks land:

| Variable | Purpose | Recommended default |
|----------|---------|---------------------|
| `CODEX_CLI_VERSION` | Selects the npm tag of `@openai/codex` installed during the image build. Align with the version validated by your automation tests. | `0.104.0` |
| `AGENT_KIT_VERSION` | Selects the npm tag of `@githubnext/spec-kit` installed alongside Codex. Keep it in sync with the Spec Kit workflows your team supports. | `0.4.0` |

Set the variables in your shell or CI pipeline and forward them with `--build-arg` once the Dockerfile exposes the matching build arguments:

```bash
export CODEX_CLI_VERSION=0.104.0
export AGENT_KIT_VERSION=0.4.0
docker build -f api_service/Dockerfile \
  --build-arg CODEX_CLI_VERSION \
  --build-arg AGENT_KIT_VERSION \
  -t moonmind/api-service:tooling .
```

CI systems that wrap Docker builds should propagate the same variables so Celery workers always ship with the expected CLI revisions as the tooling install steps roll out.

### Codex CLI verification {#codex}

After building a new image, run the following commands to ensure the bundled Codex CLI is discoverable for the non-root `app` user and matches the expected version:

```bash
docker run --rm moonmind/api-service:tooling \
  bash -lc 'whoami && which codex && codex --version'
```

- `which codex` must resolve to `/usr/local/bin/codex`.
- The `codex --version` output should match `CODEX_CLI_VERSION` passed to the Docker build.

When the Celery worker starts it now logs the detected Codex CLI path and version. Tail the worker logs to confirm the health check succeeds before triggering automation runs:

```bash
docker compose logs -f celery_codex_worker | grep -i "Codex CLI detected"
```

If either command fails, rebuild the image to restore the bundled CLI before accepting new jobs.

### Codex config drift remediation {#codex-config}

The container image now ships a managed Codex configuration template at
`/etc/codex/config.toml`, and every startup runs
`python -m api_service.scripts.ensure_codex_config` to merge the template into
`~/.codex/config.toml`. If a worker reports the config status as `drifted` or
logs that the approval policy is incorrect, follow these steps:

1. **Inspect the current policy** – Exec into the affected worker and review the
   merged file:

   ```bash
   docker compose exec celery_codex_worker bash -lc 'cat ~/.codex/config.toml'
   ```

   The `approval_policy` value must be `"never"`. Any other value indicates
   manual edits or an incomplete merge.
2. **Re-run the merge script** – From the same shell, invoke the enforcement
   helper manually to regenerate the config and capture diagnostics:

   ```bash
   python -m api_service.scripts.ensure_codex_config
   ```

   A successful run prints `[codex-config] approval policy enforced` along with
   the resolved path. Failures emit actionable errors (missing template,
   unwritable home directory, or invalid TOML).
3. **Validate health checks** – Tail the worker logs for `Codex approval policy`
   messages or call the tooling health-check endpoint to confirm the status has
   returned to `managed`:

   ```bash
   curl http://localhost:5000/internal/tooling-status | jq '.workers[] | select(.codexConfig.status != "managed")'
   ```

   An empty response confirms every worker now reports the enforced policy and
   exposes the template path plus enforcement metadata.
4. **Rebuild as needed** – If the template is missing or repeatedly drifts
   (for example, due to a bind mount that overwrites `~/.codex`), rebuild the
   image and redeploy the worker to restore the baked template before running
   additional automation.

## Step 1 – Start the automation stack

From the repository root, authenticate the Codex volume once, then start supporting services so the worker can accept automation jobs:

```bash
./tools/auth-codex-volume.sh
```

```bash
docker compose up rabbitmq celery_codex_worker api
```

Leave the stack running. The default `docker-compose.yaml` mounts the host Docker socket and provisions a shared `agentkit_workspaces` volume so the Celery worker can launch job containers while the API retains read-only access to generated artifacts. Each run receives an isolated workspace under `/work/runs/<run_id>` that persists for monitoring until you tear the stack down.【F:docker-compose.yaml†L90-L198】【F:docs/AgentKitAutomation.md†L32-L113】【F:specs/002-document-agentkit-automation/quickstart.md†L47-L106】【F:moonmind/workflows/agentkit_celery/workspace.py†L1-L48】

## Step 2 – Prepare your run request

Use Postman to compose the request that kicks off a Workflow run.

1. **Create (or select) an environment** – Add variables such as `base_url` (`http://localhost:5000`), `moonmind_api_token`, `repository`, `feature_key`, and `notes`. Keeping credentials in Postman variables prevents them from leaking into shell history while making the request reusable across workspaces.【F:specs/002-document-agentkit-automation/contracts/workflow.openapi.yaml†L11-L101】
2. **Define the request** – Inside a collection, add a `POST {{base_url}}/api/workflows/runs` request. Set the **Authorization** tab to **Bearer Token** and reference `{{moonmind_api_token}}` so the header updates automatically. Confirm the **Headers** tab includes `Content-Type: application/json`.【F:specs/002-document-agentkit-automation/contracts/workflow.openapi.yaml†L11-L80】
3. **Populate the body** – Choose **raw** + **JSON** and paste the payload below. You can replace literal values with Postman variables (for example, `"repository": "{{repository}}"` or `"featureKey": "{{feature_key}}"`) to tailor future runs without editing the JSON.
4. **Send the request** – Click **Send** to enqueue the workflow. Save the response so you can reference the returned `id` in later steps.

```json
{
  "repository": "MoonLadderStudios/moonmind",
  "featureKey": "002-document-agentkit-automation",
  "forcePhase": "discover",
  "notes": "Validate workflow automation from Postman"
}
```

## Step 3 – Trigger the automation run

Submit the payload to the Workflow API. The endpoint enqueues the Celery chain and returns the run metadata, including the `id` you will use in subsequent monitoring calls. If you need a shell-friendly alternative to Postman (for CI or scripted runs), send a matching JSON body with `curl`:

```bash
curl -sS -X POST "http://localhost:5000/api/workflows/runs" \
  -H "Authorization: Bearer ${MOONMIND_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "repository": "MoonLadderStudios/moonmind",
    "featureKey": "002-document-agentkit-automation",
    "forcePhase": "discover",
    "notes": "Validate workflow automation from curl"
  }'
```

> **Note:** The default `docker-compose.yaml` maps the FastAPI service to
> `localhost:5000`. If you change the Compose file or run the service directly,
> adjust the host and port accordingly.

The response matches the `WorkflowRun` schema defined in the Workflow contract and includes fields such as `id`, `status`, and `phase` so you can confirm the request was queued.【F:specs/002-document-agentkit-automation/contracts/workflow.openapi.yaml†L13-L80】

A successful response includes the queued status and `id`. Behind the scenes the worker allocates a workspace, starts the job container, clones the repository, and executes the Spec Kit phases in order before committing and pushing changes when a diff exists.【F:docs/AgentKitAutomation.md†L66-L112】【F:moonmind/workflows/agentkit_celery/orchestrator.py†L124-L150】

Ensure the Celery worker (or Compose stack) exports `WORKFLOW_GITHUB_REPOSITORY` and related overrides before you trigger a run so the orchestrator can clone the correct repository and configure the agent clients; these settings must align with the repository slug you pass in the request.【F:moonmind/config/settings.py†L138-L190】【F:moonmind/workflows/agentkit_celery/tasks.py†L568-L585】

## Step 4 – Monitor progress

1. **Worker logs** – Tail the Codex worker to see phase transitions and retry activity:
   ```bash
   docker compose logs -f celery_codex_worker
   ```
2. **Status API** – Poll run state (phases, branch, PR URL, artifacts) using the run identifier returned earlier:
   ```bash
   curl -H "Authorization: Bearer ${MOONMIND_API_TOKEN}" \
     "http://localhost:5000/api/workflows/runs/<run_id>"
   ```
3. **Artifacts** – Inspect logs and generated assets under `/work/runs/<run_id>/artifacts` or download them through `/api/workflows/runs/<run_id>/artifacts/<artifact_id>` as needed.【F:docs/AgentKitAutomation.md†L131-L156】【F:specs/002-document-agentkit-automation/quickstart.md†L82-L106】【F:specs/002-document-agentkit-automation/contracts/workflow.openapi.yaml†L32-L77】
   - Workflow (Celery chain) runs store Codex JSONL logs, generated patches, and GitHub responses under `var/artifacts/workflow_runs/<run_id>/`. Mount this path when running Compose locally so retries can reuse the artifacts.
   - To retry a failed Celery chain after fixing credentials, POST `/api/workflows/runs/{id}/retry` with `{"mode": "resume_failed_task"}`. The worker resumes from the failed task and reuses existing artifacts rather than recomputing patches.【F:specs/001-celery-chain-workflow/tasks.md†L85-L93】

## Step 5 – Review results and handoff

When the run reports `succeeded` (or `no_changes`), use the status response to capture:

- `branch_name` and `pull_request_url` for reviewer handoff.
- Artifact IDs for stdout/stderr, diff summaries, credential audits, and commit status logs.

Because each run uses an isolated workspace and redacts sensitive environment values, you can safely share artifacts with reviewers while leaving the Celery stack running for additional requests.【F:docs/AgentKitAutomation.md†L100-L156】

## Step 6 – Cleanup (optional)

To shut down the environment, stop the Compose stack and prune cached workspaces once all artifacts have been collected:

```bash
docker compose down --volumes
```

This removes the worker containers and deletes cached workspaces so future automation runs start from a clean slate.【F:docs/AgentKitAutomation.md†L94-L113】【F:specs/002-document-agentkit-automation/quickstart.md†L109-L118】
