Spec Kit Automation — Architecture & Operations (Option A: Persistent Codex Auth)

Status: Final
Owners: MoonMind Eng
Last Updated: Nov 5, 2025
Related Artifacts: /specs/002-document-speckit-automation/*, Celery chain guidance in specs/001-celery-chain-workflow

⸻

0. Change Summary (What’s new)

We introduce a small pool of three stateful Codex worker queues—codex-0, codex-1, codex-2—each backed by its own persistent Docker volume: codex_auth_0, codex_auth_1, codex_auth_2.
When a run needs Codex, the Celery worker launches the ephemeral job container as before, but now mounts that worker’s Codex auth volume into "$HOME/.codex" so the ChatGPT sign-in persists and is reused across many tasks.

Key additions:
- Celery routing shards Codex tasks deterministically across 3 queues.
- Job container creation mounts the selected codex_auth_* volume into "$HOME/.codex".
- Pre-flight check verifies codex login status using the job image before starting a Codex phase.
- docker-compose adds three Codex-dedicated worker services and three named volumes.
- Runbook steps to sign in once per volume using “Sign in with ChatGPT”.

⸻

1. Overview

Spec Kit Automation runs the /speckit.specify, /speckit.plan, and /speckit.tasks prompts against a target repository and packages the results as a draft Pull Request. A Celery worker launches an ephemeral job container per run, ensuring the toolchain (git, gh, Codex CLI, prompts) executes in a predictable environment while repository metadata, artifacts, and metrics are recorded for operators.

New in Option A: A small set of stateful Codex auth volumes is injected into each job container so Codex can reuse interactive ChatGPT authentication without re-logging every run.

Key responsibilities (unchanged in spirit):
1. Accept automation requests via Celery (celery_worker/speckit_worker.py).
2. Use moonmind/workflows/speckit_celery/tasks.py to orchestrate run phases.
3. Manage Docker job containers with moonmind/workflows/speckit_celery/job_container.py, now with Codex auth mounts.
4. Persist metadata with repositories.py and Alembic migrations.
5. Surface results through FastAPI.

⸻

2. Component Architecture

2.1 Celery Worker
- Containerized Celery process with Docker SDK.
- Mounts:
  - /var/run/docker.sock to control job containers.
  - Named volume speckit_workspaces → /work for shared workspaces.
- New (Option A):
  - Three dedicated worker services (celery-codex-0/1/2) each set:
    - CODEX_QUEUE to one of codex-0|1|2
    - CODEX_VOLUME_NAME to one of codex_auth_0|1|2
  - Only these workers consume Codex-bound queues.
- Emits StatsD metrics when enabled.

2.2 Job Container
- Image from SPEC_AUTOMATION_JOB_IMAGE (default moonmind/spec-automation-job:latest).
- Starts sleep infinity; Celery drives it via docker exec.
- Environment includes:
  - HOME=/work/runs/{run_id}/home (unchanged).
  - Git identity, GitHub auth tokens, agent config snapshot fields.
- New (Option A):
  - On create, we mount the worker’s Codex auth volume at "$HOME/.codex", so Codex CLI uses persistent ChatGPT OAuth.
  - Optional pre-flight check runs codex login status in a short-lived container with the same volume before the Codex phase.

2.3 Persistence Layer

Unchanged: models, lifecycle methods, and artifact registration.

2.4 API Surface

Unchanged.

⸻

3. Execution Lifecycle
1. Kickoff – kickoff_spec_run.delay(...).
2. Workspace Prep – SpecWorkspaceManager creates /work/runs/{run_id} (repo, home, artifacts).
3. Container Start – JobContainerManager.start() launches job container, now with an added Codex auth mount.
4. Discovery Phase – unchanged.
5. Submission (Codex) Phase – _run_submit_phase():
   - (New) Pre-flight: verify codex login status with the designated CODEX_VOLUME_NAME.
   - Run Codex CLI, capturing stdout/stderr artifacts.
6. Publish Phase – unchanged.
7. Finalization – unchanged.

⸻

4. Container Images & Compose Topology

| Component | Path | Notes |
|-----------|------|-------|
| Worker image | images/worker/Dockerfile | Python 3.11, Celery app, Docker SDK. |
| Job image | images/job/Dockerfile | Ubuntu 22.04, Git/GH CLI, Codex CLI, prompts, jq/curl. |
| Compose services | docker-compose.yaml | Adds three Codex worker services and three codex_auth_* volumes. |

⸻

5. Workspace Layout & Retention

```
/work
└── runs/
    └── <run_id>/
        ├── repo/        # Cloned repository
        ├── home/        # HOME for gh and runtime caches
        │   └── .codex/  # <-- Mounted from codex_auth_{0|1|2} (persistent across runs)
        └── artifacts/   # Logs, diffs, credential audit reports
```

Note: home/.codex/ is a nested mount backed by a named Docker volume. It survives job container deletion and is unique per Codex worker.

⸻

6. Credentials & Security
- Codex ChatGPT OAuth is stored under ~/.codex (mounted persistent volume).
- Do not bake tokens into images or env; rely on persistent volume + interactive login.
- Volume perms: 0700 recommended inside the container path; run as non-root user in the job image.
- The Celery worker continues to require Docker socket access.

⸻

7. Observability & Artifacts

Unchanged; add log lines around codex pre-flight and mount selection.

⸻

8. Operational Runbook
1. Provision Secrets – as before (GITHUB_TOKEN, optional agent overrides). CODEX_API_KEY is not required when using ChatGPT sign-in.
2. Authenticate Each Codex Volume Once (three times total):
   - On a machine with a browser:

```bash
docker run --rm -it \
  -v codex_auth_0:/home/app/.codex \
  --entrypoint bash \
  ${SPEC_AUTOMATION_JOB_IMAGE:-moonmind/spec-automation-job:latest} -lc 'codex login && codex login status'
# Repeat for codex_auth_1 and codex_auth_2
```

   If you must authenticate elsewhere, copy your local ~/.codex into each volume with care (avoid duplicating to many places).
3. Start Services – `docker compose up -d rabbitmq api celery-codex-0 celery-codex-1 celery-codex-2` (and any non-Codex workers).
4. Dispatch Runs – unchanged.
5. Monitor – Celery logs will show which codex-* queue and which codex_auth_* volume were selected. Operators can also poll `/api/workflows/speckit/codex/shards` for the current shard inventory:

   ```bash
   curl -s \
     -H "Authorization: Bearer ${API_TOKEN}" \
     https://api.moonmind.local/api/workflows/speckit/codex/shards | jq
   ```

   The response lists each queue, its mapped volume, the volumeStatus (`ready`, `needs_auth`, or `error`), and the latest pre-flight outcome recorded for that shard.
6. Remediate – When a shard reports `latestPreflightStatus: "failed"` or `volumeStatus: "needs_auth"`, trigger a targeted refresh for the affected run:

   ```bash
   curl -s -X POST \
     -H "Authorization: Bearer ${API_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"forceRefresh": true}' \
     https://api.moonmind.local/api/workflows/speckit/runs/<run_id>/codex/preflight | jq
   ```

   A `status: "passed"` response updates the run and marks the auth volume `ready`. If it returns `failed`, re-run `codex login` for the reported `volumeName` before retrying the workflow.
7. Cleanup – speckit_workspaces still prunes per TTL; Codex volumes persist and should not be removed unless you intend to re-authenticate.

⸻

9. Testing & Validation
- Unit & integration tests remain valid; add a smoke test to assert:
  - JobContainerManager attaches the configured CODEX_VOLUME_NAME to "$HOME/.codex".
  - Pre-flight codex login status passes before Codex work begins.

⸻

10. Reference Environment Variables (Additions)

| Variable | Purpose |
|----------|---------|
| CODEX_QUEUE | Queue name for this Codex worker (codex-0, codex-1, codex-2). |
| CODEX_VOLUME_NAME | Name of the Docker volume that stores ChatGPT OAuth for this worker (codex_auth_0/1/2). |
| CODEX_SHARDS | Optional; default 3 used in routing helper. |
| CODEX_LOGIN_CHECK_IMAGE | Job image used for pre-flight status check; defaults to SPEC_AUTOMATION_JOB_IMAGE. |

(All previous variables remain in effect.)

⸻

11. Rollout Considerations
1. Stand up the three Codex workers alongside existing components.
2. Authenticate each codex_auth_* volume (one time each).
3. Validate with a dry-run; then enable for allow-listed repos.
4. If later you add more workers, increase CODEX_SHARDS and add volumes/services accordingly.

⸻

12. Implementation Details & Snippets

12.1 Celery routing (new sharding to 3 queues)

Create or update celeryconfig.py:

```python
# celeryconfig.py
from kombu import Queue
import hashlib
import os

CODEX_SHARDS = int(os.getenv("CODEX_SHARDS", "3"))

task_queues = tuple(
    [Queue("default")] + [Queue(f"codex-{i}") for i in range(CODEX_SHARDS)]
)

def _codex_shard(key: str) -> str:
    h = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)
    return f"codex-{h % CODEX_SHARDS}"

def route_task(name, args, kwargs, options, task=None, **kw):
    # Route all Codex-bound tasks to the sharded codex-* queues
    if name.startswith("speckit.codex."):
        # Pick a stable affinity key if available
        key = (kwargs.get("project_id")
               or kwargs.get("repo")
               or kwargs.get("affinity_key")
               or name)
        return {"queue": _codex_shard(key)}
    return {"queue": "default"}

task_routes = (route_task,)
worker_prefetch_multiplier = 1
task_acks_late = True
worker_max_tasks_per_child = 100
```

If you prefer explicit routing, you can set queue="codex-0|1|2" from the caller and skip hashing.

⸻

12.2 Job container: mount ~/.codex from the worker’s volume

Update moonmind/workflows/speckit_celery/job_container.py (creation path only):

```diff
@@
-from docker.types import Mount
+from docker.types import Mount
+import os
 
 class JobContainerManager:
     def start(self, run_id: str, environment: dict) -> Container:
         home = f"/work/runs/{run_id}/home"
         mounts = [
             Mount(target="/work", source="speckit_workspaces", type="volume"),
         ]
+        # Option A: persistent Codex auth volume for this worker
+        codex_volume = os.getenv("CODEX_VOLUME_NAME")
+        if codex_volume:
+            mounts.append(
+                Mount(
+                    target=f"{home}/.codex",   # nested mount at $HOME/.codex
+                    source=codex_volume,
+                    type="volume",
+                    read_only=False,
+                )
+            )
 
         container = self._client.containers.run(
             image=os.getenv("SPEC_AUTOMATION_JOB_IMAGE", "moonmind/spec-automation-job:latest"),
             command=["sleep", "infinity"],
             environment={
                 **environment,
                 "HOME": home,
             },
             mounts=mounts,
             detach=True,
         )
         return container
```

Optional (recommended) pre-flight check before Codex work (e.g., inside _run_submit_phase() or submit_codex_job):

```python
from docker import from_env
from docker.types import Mount

def _assert_codex_logged_in():
    codex_volume = os.getenv("CODEX_VOLUME_NAME")
    if not codex_volume:
        return  # no check if not configured
    image = os.getenv(
        "CODEX_LOGIN_CHECK_IMAGE",
        os.getenv("SPEC_AUTOMATION_JOB_IMAGE", "moonmind/spec-automation-job:latest"),
    )
    client = from_env()
    # Short-lived check container
    container = client.containers.run(
        image=image,
        command=["bash", "-lc", "codex login status"],
        mounts=[Mount(target="/home/app/.codex", source=codex_volume, type="volume")],
        user="1000:1000",
        detach=True,
        auto_remove=True,
    )
    exit_code = container.wait()["StatusCode"]
    if exit_code != 0:
        raise RuntimeError("Codex CLI not authenticated for this worker (run `codex login`).")
```

Call `_assert_codex_logged_in()` at the start of your Codex submission phase.

⸻

12.3 docker-compose: three Codex workers + three volumes

Append to docker-compose.yaml:

```yaml
services:
  celery-codex-0:
    image: your/app:latest
    command: >
      celery -A moonmind.workflows.speckit_celery.app worker
      -n codex0@%h -Q codex-0 --concurrency=2
    environment:
      - CODEX_QUEUE=codex-0
      - CODEX_VOLUME_NAME=codex_auth_0
      - SPEC_AUTOMATION_JOB_IMAGE=${SPEC_AUTOMATION_JOB_IMAGE:-moonmind/spec-automation-job:latest}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - speckit_workspaces:/work
    depends_on: [rabbitmq]

  celery-codex-1:
    image: your/app:latest
    command: >
      celery -A moonmind.workflows.speckit_celery.app worker
      -n codex1@%h -Q codex-1 --concurrency=2
    environment:
      - CODEX_QUEUE=codex-1
      - CODEX_VOLUME_NAME=codex_auth_1
      - SPEC_AUTOMATION_JOB_IMAGE=${SPEC_AUTOMATION_JOB_IMAGE:-moonmind/spec-automation-job:latest}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - speckit_workspaces:/work
    depends_on: [rabbitmq]

  celery-codex-2:
    image: your/app:latest
    command: >
      celery -A moonmind.workflows.speckit_celery.app worker
      -n codex2@%h -Q codex-2 --concurrency=2
    environment:
      - CODEX_QUEUE=codex-2
      - CODEX_VOLUME_NAME=codex_auth_2
      - SPEC_AUTOMATION_JOB_IMAGE=${SPEC_AUTOMATION_JOB_IMAGE:-moonmind/spec-automation-job:latest}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - speckit_workspaces:/work
    depends_on: [rabbitmq]

volumes:
  speckit_workspaces:
  codex_auth_0:
  codex_auth_1:
  codex_auth_2:
```

The worker services themselves do not need the codex_auth_* volumes mounted; those are attached directly to job containers at runtime when they’re created via the Docker API.

⸻

12.4 Task wrapper (Codex CLI, non-interactive)

Keep your non-interactive Codex execution pattern and ensure prompts don’t require approvals:

```python
# moonmind/workflows/speckit_celery/tasks.py
@shared_task(name="speckit.codex.exec_plan", bind=True)
def exec_plan(self, repo_path: str, plan_prompt: str):
    _assert_codex_logged_in()  # optional check from 12.2
    cmd = [
        "codex",
        "--cwd", repo_path,
        "--ask-for-approval", "never",
        "--sandbox", "auto",
        "exec", plan_prompt,
    ]
    return _run_cli(cmd)  # your existing helper capturing stdout/stderr to artifacts
```

⸻

13. Failure Modes & Recovery
- “Not authenticated” at runtime → Pre-flight raises; run codex login into the corresponding codex_auth_* volume and retry.
- Two workers refresh token simultaneously → With one volume per worker, collisions are avoided.
- Permissions → Ensure job image runs as the same non-root UID that owns ~/.codex (1000:1000 recommended).

⸻

14. Appendix

14.1 Manual volume inspection

```bash
docker run --rm -it -v codex_auth_0:/dst alpine ls -la /dst
```

You should see files like auth.json after a successful login.

14.2 One-time login via headless jump

Authenticate locally (with browser), then copy ~/.codex into a volume:

```bash
# Tar up your local ~/.codex
tar czf codex.tgz -C $HOME .codex
# Load into volume
docker run --rm -i -v codex_auth_1:/dst alpine sh -lc "tar xzf - -C /dst" < codex.tgz
```

⸻

⸻

15. Release Notes

- **2025-11-05 – Celery OAuth volume polish**: Executed targeted regression coverage after sharded routing and Codex auth updates.
  - `poetry run pytest tests/unit/workflows/test_tasks.py` (Spec workflow task and routing unit suite) – passed.
  - `poetry run pytest tests/contract/test_workflow_api.py` (Codex shard health and pre-flight contract) – passed.

That’s it. With these changes, you preserve the existing ephemeral job-container model while ensuring Codex CLI stays signed in via three persistent, reusable authentication volumes—cleanly mapped to three dedicated Codex worker queues.
