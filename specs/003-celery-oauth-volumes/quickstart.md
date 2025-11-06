# Quickstart: Celery OAuth Volume Mounts

## 1. Prerequisites
- Docker Engine + Docker Compose v2 installed with access to `/var/run/docker.sock`.
- ChatGPT account with permission to authenticate Codex CLI.
- SPEC_AUTOMATION_JOB_IMAGE pushed or available locally (default `moonmind/spec-automation-job:latest`).
- Git repository synced to branch `001-celery-oauth-volumes`.

## 2. Prepare Codex Auth Volumes
1. Create the three named volumes if they do not already exist:
   ```bash
   docker volume create codex_auth_0
   docker volume create codex_auth_1
   docker volume create codex_auth_2
   ```
2. Authenticate each volume once using a browser-enabled environment:
   ```bash
   export JOB_IMAGE=${SPEC_AUTOMATION_JOB_IMAGE:-moonmind/spec-automation-job:latest}
   for shard in 0 1 2; do
     docker run --rm -it \
       -v codex_auth_${shard}:/home/app/.codex \
       "${JOB_IMAGE}" \
       bash -lc 'codex login && codex login status'
   done
   ```
3. Record the authentication timestamp for operations log.

## 3. Launch Services
1. Bring up infrastructure and Codex workers:
   ```bash
   docker compose up -d rabbitmq api celery-codex-0 celery-codex-1 celery-codex-2
   ```
2. Verify workers register on the expected queues:
   ```bash
   docker compose logs -f celery-codex-0
   ```
   Confirm log lines include `Listening on codex-0`.

## 4. Trigger a Spec Kit Run
1. Dispatch a run that exercises the Codex phase:
   ```bash
   poetry run python -m tools.trigger_spec_run --repo <repo_url> --prompt speckit.specify
   ```
2. Observe Celery logs for entries such as:
   ```text
   Spec workflow task submit: queue=codex-1 volume=codex_auth_1 preflight=passed
   ```

## 5. Monitor & Remediate
- Poll shard health without shelling into containers:
  ```bash
  curl -s https://api.moonmind.local/api/workflows/speckit/codex/shards \
       -H 'Authorization: Bearer <token>' | jq '.shards[] | {queueName, volumeName, volumeStatus, latestPreflightStatus}'
  ```
- If logs or the API report `latestPreflightStatus` as `failed`, trigger the pre-flight endpoint for the affected run:
  ```bash
  curl -s -X POST https://api.moonmind.local/api/workflows/speckit/runs/<run_id>/codex/preflight \
       -H 'Authorization: Bearer <token>' \
       -H 'Content-Type: application/json' \
       -d '{}'
  ```
- A `status` of `passed` marks the backing volume `ready`; if `failed`, re-run Step 2 to reauthenticate that shard before retrying the workflow.

## 6. Shutdown
- To stop Codex workers and preserve volumes:
  ```bash
  docker compose stop celery-codex-0 celery-codex-1 celery-codex-2
  ```
- Volumes remain intact; remove them only if you intend to re-authenticate.
