# Spec Kit Automation Instructions

These instructions describe how to launch the Spec Kit automation pipeline so that, after you supply credentials, the desired repository, and the Spec Kit input text, the system handles cloning, running `/speckit.specify`, `/speckit.plan`, and `/speckit.tasks`, committing any changes, and opening a draft pull request automatically.【F:docs/SpecKitAutomation.md†L12-L78】【F:specs/002-document-speckit-automation/spec.md†L11-L45】

## Prerequisites

1. **Runtime services** – Bring up RabbitMQ, the Spec Kit Celery worker, and the API service together; they share the automation queue, result backend, and REST surface used to trigger and monitor runs.【F:docs/SpecKitAutomation.md†L32-L95】【F:specs/002-document-speckit-automation/quickstart.md†L47-L55】
2. **Credentials** – Export a GitHub token with `repo` scope and the Codex API key so the job container can clone, push, and execute Spec Kit prompts. Set `SPEC_WORKFLOW_TEST_MODE=true` when you only want to dry-run without pushing changes.【F:specs/002-document-speckit-automation/quickstart.md†L32-L43】【F:docs/SpecKitAutomation.md†L116-L129】
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
docker compose up rabbitmq celery-worker api
```

Leave the stack running. The default `docker-compose.yaml` now mounts the host Docker socket and provisions a shared `speckit_workspaces` volume so the Celery worker can launch job containers while the API retains read-only access to generated artifacts. Each run receives an isolated workspace under `/work/runs/<run_id>` that persists for monitoring until you tear the stack down.【F:docker-compose.yaml†L90-L198】【F:docs/SpecKitAutomation.md†L32-L113】【F:specs/002-document-speckit-automation/quickstart.md†L47-L106】【F:moonmind/workflows/speckit_celery/workspace.py†L1-L48】

## Step 2 – Prepare your run request

In a second terminal, load your credentials and craft the JSON payload that identifies the target repository, optional base branch, the Spec Kit input text, and any optional metadata overrides. The snippet below uses `jq` to safely assemble the request while avoiding temporary files with secrets:

```bash
# Pull credentials from your preferred secret manager so they never land in
# shell history. The example below uses the 1Password CLI; substitute `pass`,
# `aws secretsmanager`, or another tool that fits your environment.
export GITHUB_TOKEN="$(op read 'op://moonmind/github-token/value')"
export CODEX_API_KEY="$(op read 'op://moonmind/codex-api-key/value')"
export MOONMIND_API_TOKEN="$(op read 'op://moonmind/moonmind-api-token/value')"

export TARGET_REPO="MoonLadderStudios/moonmind" # repository slug (owner/name)
export BASE_BRANCH="main"                      # optional: defaults to main if empty
export SPEC_INPUT_FILE="spec-inputs/002.md"    # path to your Spec Kit text
export SPEC_AUTOMATION_DRY_RUN="false"         # optional: true|false
export SPEC_AUTOMATION_EXTERNAL_REF=""         # optional: correlation ID for auditing

REQUEST_BODY=$(jq -n \
  --arg repo "$TARGET_REPO" \
  --arg base "$BASE_BRANCH" \
  --rawfile specify "$SPEC_INPUT_FILE" \
  --arg dry "$SPEC_AUTOMATION_DRY_RUN" \
  --arg external "$SPEC_AUTOMATION_EXTERNAL_REF" \
  '({"repository": $repo, "specify_text": $specify}
    + (if ($base | length) > 0 then {"base_branch": $base} else {} end)
    + (if ($dry | length) > 0 then {"dry_run": ($dry | test("(?i)^(1|true|yes)$"))} else {} end)
    + (if ($external | length) > 0 then {"external_ref": $external} else {} end))'
)
```

If you cannot rely on a secrets manager, temporarily disable shell history
(`set +o history`) before exporting the variables, and remember to `unset` them
once the run finishes.

## Step 3 – Trigger the automation run

Submit the payload to the Spec Automation API. The endpoint enqueues the Celery chain and returns the run metadata, including the `run_id` you will use in subsequent monitoring calls:

```bash
printf '%s' "$REQUEST_BODY" | \
curl -sS -X POST "http://localhost:5000/api/spec-automation/runs" \
  -H "Authorization: Bearer ${MOONMIND_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d @-
```

> **Note:** The default `docker-compose.yaml` maps the FastAPI service to
> `localhost:5000`. If you change the Compose file or run the service directly,
> adjust the host and port accordingly.

The response matches the `RunResponse` schema defined in the Spec Automation contract and includes fields such as `run_id`, `status`, and `accepted_at` so you can confirm the request was queued.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L13-L80】

A successful response includes the queued status and `run_id`. Behind the scenes the worker allocates a workspace, starts the job container, clones the repository, and executes the Spec Kit phases in order before committing and pushing changes when a diff exists.【F:docs/SpecKitAutomation.md†L66-L112】【F:moonmind/workflows/speckit_celery/orchestrator.py†L124-L150】

Ensure the Celery worker (or Compose stack) exports `SPEC_WORKFLOW_GITHUB_REPOSITORY` and related overrides before you trigger a run so the orchestrator can clone the correct repository and configure the agent clients; these settings must align with the repository slug you pass in the request.【F:moonmind/config/settings.py†L138-L190】【F:moonmind/workflows/speckit_celery/tasks.py†L568-L585】

### Using Postman to submit the request

If you prefer a graphical client, you can send the same request through Postman:

1. **Create a collection (optional)** – Add a new collection called “Spec Automation” and define a collection-level variable named `base_url` with the value `http://localhost:5000` so requests automatically follow your environment. You can mirror environment-specific overrides (e.g., staging vs. production) by creating additional Postman environments.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L101】
2. **Add the request** – Create a `POST {{base_url}}/api/spec-automation/runs` request inside the collection. Set the **Authorization** type to **Bearer Token** and paste the `MOONMIND_API_TOKEN` value captured earlier; Postman will include it as the `Authorization: Bearer …` header on send. Set the **Headers** tab to include `Content-Type: application/json` if it is not automatically populated.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L80】
3. **Populate the body** – In the **Body** tab, choose **raw** + **JSON** and paste the same payload you generated above. You can reference Postman variables (e.g., `{{repository}}`, `{{base_branch}}`, `{{specify_text}}`) to avoid editing raw values for future runs. When you need to send file contents, paste the text directly into the JSON string or use Postman’s `pm.sendRequest` pre-request script to read from a local file and inject it into the body before send.【F:docs/SpecKitAutomation.md†L66-L112】【F:specs/002-document-speckit-automation/spec.md†L11-L45】
4. **Send and inspect** – Click **Send**. The response pane displays the `RunResponse` payload, including the `run_id`. Save the response to the collection if you plan to reuse the schema for regression testing or share the run metadata with reviewers.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L13-L80】

Postman saves the request history, so after the first configuration you can re-run the automation by updating only the repository slug or Spec input variables. Pair the collection with Postman’s built-in environments to toggle between local development and remote deployments without rewriting the request body.【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L101】

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
