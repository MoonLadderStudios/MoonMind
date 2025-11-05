# Spec Kit Automation Instructions

These instructions describe how to launch the Spec Kit automation pipeline so that, after you supply credentials, the desired repository, and the Spec Kit input text, the system handles cloning, running `/speckit.specify`, `/speckit.plan`, and `/speckit.tasks`, committing any changes, and opening a draft pull request automatically—now with persistent Codex authentication that is sharded across three dedicated worker queues and volumes.【F:docs/SpecKitAutomation.md†L10-L83】【F:specs/002-document-speckit-automation/spec.md†L11-L45】

## Prerequisites

1. **Runtime services** – Bring up RabbitMQ, the Spec Kit API service, and all Celery workers (the default worker plus the three Codex shards) so they share the automation queue, result backend, and REST surface used to trigger and monitor runs.【F:docs/SpecKitAutomation.md†L39-L92】【F:specs/002-document-speckit-automation/quickstart.md†L47-L55】
2. **Credentials** – Export a GitHub token with `repo` scope so the job container can clone, push, and execute Spec Kit prompts. The Codex CLI now uses ChatGPT OAuth that is stored in the codex_auth_* volumes instead of a static API key; plan time to complete the interactive login described below. Set `SPEC_WORKFLOW_TEST_MODE=true` when you only want to dry-run without pushing changes.【F:docs/SpecKitAutomation.md†L112-L143】【F:specs/002-document-speckit-automation/quickstart.md†L32-L43】
3. **Spec input** – Prepare the text (YAML/JSON/Markdown) you want to feed into `/speckit.specify`; save it locally so it can be injected into the run request body.【F:specs/002-document-speckit-automation/spec.md†L11-L45】
4. **API access** – Obtain an access token for the MoonMind API (e.g., via Keycloak) because `/api/spec-automation` endpoints require authentication.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L101】 If you are using the bundled Keycloak realm, walk through the steps below and then export the resulting bearer token as `MOONMIND_API_TOKEN`.
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

## Step 1 – Start the automation stack

From the repository root, start the supporting services in one terminal so the worker can accept automation jobs:

```bash
docker compose up rabbitmq api celery-worker celery-codex-0 celery-codex-1 celery-codex-2
```

Leave the stack running. The default `docker-compose.yaml` now mounts the host Docker socket, provisions a shared `speckit_workspaces` volume, and introduces codex_auth_* volumes that are wired to the Codex workers so every run can mount the correct persistent ChatGPT credentials under `$HOME/.codex`. Each run receives an isolated workspace under `/work/runs/<run_id>` that persists for monitoring until you tear the stack down.【F:docs/SpecKitAutomation.md†L39-L109】【F:specs/002-document-speckit-automation/quickstart.md†L47-L106】【F:moonmind/workflows/speckit_celery/workspace.py†L1-L48】

### Step 1a – Authenticate Codex volumes (Sign in with ChatGPT)

Run the ChatGPT sign-in flow once per Codex worker so the associated `codex_auth_*` volume stores the OAuth session that will be mounted into future job containers. Exec into each Codex worker and launch the job image with the worker’s volume attached to `$HOME/.codex` to complete `codex login` and immediately verify the status before exiting:

```bash
# Worker 0
docker compose exec celery-codex-0 bash -lc '
  CODEX_VOLUME_NAME=${CODEX_VOLUME_NAME:-codex_auth_0}
  SPEC_AUTOMATION_JOB_IMAGE=${SPEC_AUTOMATION_JOB_IMAGE:-moonmind/spec-automation-job:latest}
  docker run --rm -it \
    -e HOME=/work/codex-home \
    -v ${CODEX_VOLUME_NAME}:/work/codex-home/.codex \
    ${SPEC_AUTOMATION_JOB_IMAGE} \
    bash -lc "codex login && codex login status"
'

# Repeat for celery-codex-1/2, swapping the CODEX volume name to codex_auth_1 and codex_auth_2.
```

When the CLI opens a browser prompt, choose **Sign in with ChatGPT** and finish the OAuth flow. The `codex login status` check should succeed before you close the session. If you ever prune a codex_auth_* volume, repeat the same steps to restore the login for that worker.【F:docs/SpecKitAutomation.md†L47-L143】

## Step 2 – Prepare your run request

Use Postman to compose the request that kicks off a Spec Automation run.

1. **Create (or select) an environment** – Add variables such as `base_url` (`http://localhost:5000`), `moonmind_api_token`, `repository`, `base_branch`, and `spec_input`. Keeping credentials in Postman variables prevents them from leaking into shell history while making the request reusable across workspaces.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L101】
2. **Define the request** – Inside a collection, add a `POST {{base_url}}/api/spec-automation/runs` request. Set the **Authorization** tab to **Bearer Token** and reference `{{moonmind_api_token}}` so the header updates automatically. Confirm the **Headers** tab includes `Content-Type: application/json`.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L80】
3. **Populate the body** – Choose **raw** + **JSON** and paste the payload below. You can replace literal values with Postman variables (for example, `"repository": "{{repository}}"` or `"specify_text": "{{spec_input}}"`) to tailor future runs without editing the JSON.
4. **Send the request** – Click **Send** to enqueue the workflow. Save the response so you can reference the returned `run_id` in later steps.

```json
{
  "repository": "MoonLadderStudios/moonmind",
  "base_branch": "main",
  "specify_text": "# Spec title\n\n- Goal: Validate automation from Postman\n- Scope: /speckit.specify, /speckit.plan, /speckit.tasks",
  "dry_run": false,
  "external_ref": "postman-demo-001"
}
```

## Step 3 – Trigger the automation run

Submit the payload to the Spec Automation API. The endpoint enqueues the Celery chain and returns the run metadata, including the `run_id` you will use in subsequent monitoring calls. If you need a shell-friendly alternative to Postman (for CI or scripted runs), send the same JSON body with `curl`:

```bash
curl -sS -X POST "http://localhost:5000/api/spec-automation/runs" \
  -H "Authorization: Bearer ${MOONMIND_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "repository": "MoonLadderStudios/moonmind",
    "base_branch": "main",
    "specify_text": "# Spec title\n\n- Goal: Validate automation from Postman\n- Scope: /speckit.specify, /speckit.plan, /speckit.tasks",
    "dry_run": false,
    "external_ref": "postman-demo-001"
  }'
```

> **Note:** The default `docker-compose.yaml` maps the FastAPI service to
> `localhost:5000`. If you change the Compose file or run the service directly,
> adjust the host and port accordingly.

The response matches the `RunResponse` schema defined in the Spec Automation contract and includes fields such as `run_id`, `status`, and `accepted_at` so you can confirm the request was queued.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L13-L80】

A successful response includes the queued status and `run_id`. Behind the scenes the worker allocates a workspace, starts the job container, clones the repository, and executes the Spec Kit phases in order before committing and pushing changes when a diff exists.【F:docs/SpecKitAutomation.md†L66-L112】【F:moonmind/workflows/speckit_celery/orchestrator.py†L124-L150】

Ensure the Celery worker (or Compose stack) exports `SPEC_WORKFLOW_GITHUB_REPOSITORY` and related overrides before you trigger a run so the orchestrator can clone the correct repository and configure the agent clients; these settings must align with the repository slug you pass in the request.【F:moonmind/config/settings.py†L138-L190】【F:moonmind/workflows/speckit_celery/tasks.py†L568-L585】

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
