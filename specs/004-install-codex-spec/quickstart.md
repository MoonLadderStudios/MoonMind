# Quickstart: Codex & Spec Kit Tooling Availability

Follow these steps to build the updated `api_service` image, confirm Codex + Spec Kit CLIs are bundled, and verify Celery workers inherit the non-interactive Codex config.

## 1. Prerequisites

- Docker Engine with access to the MoonMind repo root.
- Network access (or cached artifacts) to fetch npm packages `@githubnext/codex-cli` and `@githubnext/spec-kit` during the build.
- ChatGPT/Codex credentials already stored in the worker’s persistent `~/.codex` volume (only needed for `codex login status`).
- RabbitMQ, PostgreSQL, and Celery worker services available if you plan to run the full workflow (`docker compose up rabbitmq celery-worker api`).

## 2. Build the image with pinned versions

```bash
# From the repo root
CODEX_CLI_VERSION=0.6.0 SPEC_KIT_VERSION=0.4.0 \
  docker build -t moonmind/api-service:tooling \
  --build-arg CODEX_CLI_VERSION \
  --build-arg SPEC_KIT_VERSION \
  -f api_service/Dockerfile .
```

- The Dockerfile installs Node.js in a builder stage, runs `npm install -g @githubnext/codex-cli@${CODEX_CLI_VERSION}` and `@githubnext/spec-kit@${SPEC_KIT_VERSION}`, then copies the binaries into the final runtime layer.
- Build logs should show `codex --version` and `speckit --version` checks before the image finalizes.

## 3. Verify CLI availability in a container

```bash
docker run --rm moonmind/api-service:tooling bash -lc '
  which codex && codex --version && 
  which speckit && speckit --help >/dev/null'
```

- Both `which` commands must resolve to `/usr/local/bin/...` for the non-root `app` user.
- Failure indicates the PATH or ownership in the Dockerfile needs review.

## 4. Enforce `approval_policy = "never"`

```bash
docker run --rm -e HOME=/home/app moonmind/api-service:tooling bash -lc '
  cat ~/.codex/config.toml'
```

- The merge script runs at container start and ensures the file contains `approval_policy = "never"` even if other keys exist.
- To test drift handling, mount a volume with a conflicting config; the entrypoint should rewrite the policy and log the correction.

## 5. Run the Spec Kit smoke test

```bash
docker compose run --rm celery-worker bash -lc '
  speckit --version && codex login status && \
  python -m moonmind.workflows.speckit_celery.tasks --help'
```

- Confirms the Celery worker (which reuses the api_service image) can access both CLIs and that Codex authentication remains non-interactive.
- Watch the worker logs for `Spec Kit CLI detected` entries before task output; these come from the new bootstrap check inside `moonmind/workflows/speckit_celery/tasks.py` and confirm the binary is executable for the `app` user.
- The command should exit with status zero; if the `speckit` invocation fails, rebuild the image and confirm the Dockerfile logs `speckit --version` during the builder stage.

## 6. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `codex: command not found` | Builder layer binaries not copied into runtime image | Ensure Dockerfile copies `/usr/local/bin/codex` from the Node stage and sets executable perms |
| `approval_policy` missing from config | Merge script failed or HOME not writable | Verify entrypoint runs under the correct UID/GID and HOME resolves to `/home/app` |
| `speckit` exits due to missing Node deps | npm global install skipped dev deps | Re-run build with clean cache and confirm `npm config set fund false` doesn’t block dependencies |

Once all checks pass, tag/push the image and proceed to `/speckit.tasks` planning.
