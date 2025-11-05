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

Leave the stack running; the Celery worker mounts `/var/run/docker.sock` and the shared `speckit_workspaces` volume so that each run gets an isolated workspace under `/work/runs/<run_id>`.【F:docs/SpecKitAutomation.md†L32-L113】【F:specs/002-document-speckit-automation/quickstart.md†L47-L106】

## Step 2 – Prepare your run request

In a second terminal, load your credentials and craft the JSON payload that names the target repository, optional base branch, and the specification text. The snippet below uses `jq` to safely embed multi-line input:

```bash
# Pull credentials from your preferred secret manager so they never land in
# shell history. The example below uses the 1Password CLI; substitute `pass`,
# `aws secretsmanager`, or another tool that fits your environment.
export GITHUB_TOKEN="$(op read 'op://moonmind/github-token/value')"
export CODEX_API_KEY="$(op read 'op://moonmind/codex-api-key/value')"
export MOONMIND_API_TOKEN="$(op read 'op://moonmind/moonmind-api-token/value')"

export TARGET_REPO="owner/example-repo"
export SPEC_INPUT_FILE="path/to/spec-input.md"
export BASE_BRANCH="main"                     # optional override
export SPEC_AUTOMATION_DRY_RUN="false"        # set to "true" to skip push/PR

REQUEST_BODY=$(jq -n \
  --arg repo "$TARGET_REPO" \
  --arg base "$BASE_BRANCH" \
  --rawfile specify "$SPEC_INPUT_FILE" \
  --arg dry "$SPEC_AUTOMATION_DRY_RUN" \
  '{"repository": $repo, "base_branch": $base, "specify_text": $specify, "dry_run": ($dry == "true")}'
)
```

If you cannot rely on a secrets manager, temporarily disable shell history
(`set +o history`) before exporting the variables, and remember to `unset` them
once the run finishes.

The automation records the supplied spec input in the `requested_spec_input` field and stores it alongside all phase artifacts for future auditing.【F:moonmind/workflows/speckit_celery/models.py†L362-L413】【F:moonmind/workflows/speckit_celery/repositories.py†L477-L510】

## Step 3 – Trigger the automation run

Submit the payload to the Spec Automation API. The endpoint returns a run identifier that you can use to poll status and fetch artifacts:

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

A successful response includes the queued status and `run_id`. Behind the scenes the worker allocates a workspace, starts the job container, clones the repository, and executes the Spec Kit phases in order before committing and pushing changes when a diff exists.【F:docs/SpecKitAutomation.md†L66-L112】【F:specs/002-document-speckit-automation/contracts/spec-automation.openapi.yaml†L11-L154】

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
