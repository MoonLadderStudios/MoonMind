# Spec Kit Automation — Technical Design Document

**Status:** Draft
**Owners:** MoonMind Eng
**Last updated:** Nov 3, 2025
**Related prior art:** `specs\001-celery-chain-workflow` (Celery chain best practices & context propagation)

---

## 1) Goal & Scope (unchanged)

Provide an automated pipeline that:

1. Accepts a **Celery** task with `specify_text` and `repo` (plus optional flags).
2. Clones the repo **inside a short-lived job container**; sets `HOME` for Spec Kit + Codex CLI.
3. Runs, in order:

   * `codex /prompts:speckit.specify "<specify_text>"`
   * `codex /prompts:speckit.plan`
   * `codex /prompts:speckit.tasks`
4. Commits changes to a new branch and **opens a Pull Request**.
5. Keeps the “agent” backend **swappable** (Codex CLI by default).

**Key change vs earlier draft:** Celery **workers themselves run in Docker** and orchestrate *per-run job containers* via the Docker socket (DooD). A **named volume** (`speckit_workspaces`) holds per-run workspaces shared between worker and job containers.

---

## 2) High-Level Architecture

### 2.1 Components

* **Celery Worker (containerized)**

  * Runs the Celery app and holds the Docker client (CLI or SDK).
  * Mounts:

    * `speckit_workspaces` (named volume) → `/work`
    * `/var/run/docker.sock` (to launch job containers)
  * Starts/stops a **job container** per run; passes secrets as env vars.

* **Job Container (ephemeral, per run)**

  * Contains the toolchain: `git`, `gh` CLI, **Codex CLI** (default agent), Spec Kit prompts, bash, jq, etc.
  * Mounts `speckit_workspaces:/work`.
  * Uses **unique per-run directories**:

    * workspace: `/work/runs/{run_id}`
    * repo path: `/work/runs/{run_id}/repo`
    * **HOME**: `/work/runs/{run_id}/home`

* **GitHub Integration**

  * All git/gh operations run **inside the job container** (ensures consistent git identity & HOME).

* **Artifact Store (optional)**

  * Persist logs and outputs under `/work/runs/{run_id}/artifacts` (backed by the named volume, or uploaded afterwards).

### 2.2 Sequence Diagram (updated)

```mermaid
sequenceDiagram
  autonumber
  participant P as Producer / API
  participant B as Celery Broker
  participant W as Celery Worker (container)
  participant D as Docker Engine (host)
  participant J as Job Container (ephemeral)
  participant GH as GitHub

  P->>B: enqueue kickoff(specify_text, repo, opts)
  B->>W: deliver task
  W->>D: docker run --name job-{run_id} -v speckit_workspaces:/work ...
  D-->>W: container id
  W->>J: prepare /work/runs/{run_id}/...; set HOME
  W->>J: git clone; checkout -b branch
  W->>J: codex /prompts:speckit.specify "<text>"
  W->>J: codex /prompts:speckit.plan
  W->>J: codex /prompts:speckit.tasks
  W->>J: git add/commit/push
  W->>GH: gh pr create (inside J)
  GH-->>W: PR URL
  W-->>P: result {run_id, pr_url, logs}
  W->>D: docker rm -f job-{run_id}
```

---

## 3) Container Images

### 3.1 Worker Image (lightweight)

* Purpose: run Celery; orchestrate Docker.
* Includes: Python 3.11, Celery, Docker CLI or SDK, minimal shell utils.
* Does **not** need Codex CLI or build toolchains.

```Dockerfile
# ./images/worker/Dockerfile
FROM python:3.11-slim
RUN pip install celery docker pydantic[dotenv]  # + your app deps
WORKDIR /app
COPY app/ /app
ENV PYTHONUNBUFFERED=1
# ENTRYPOINT provided by compose: celery -A app.celery_app worker ...
```

### 3.2 Job Image (toolchain)

* Purpose: run Spec Kit phases and git/gh tasks.
* Includes: `git`, `gh`, **Codex CLI** + Spec Kit prompts, bash, curl, jq, language runtimes if needed.
* Version-pinned for reproducibility.

```Dockerfile
# ./images/job/Dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    git gh curl jq bash ca-certificates python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

# Install Codex CLI & Spec Kit prompts (pin versions)
# RUN curl -fsSL ... | bash
# COPY prompts/ /usr/local/share/spec-kit/prompts/

ENV PATH="/usr/local/bin:${PATH}"
```

---

## 4) docker-compose

```yaml
version: "3.9"
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  worker:
    build: ./images/worker
    command: celery -A app.celery_app worker --loglevel=INFO -Q speckit --concurrency=2
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - CELERY_RESULT_BACKEND=redis://redis:6379/1
      - AGENT_BACKEND=codex
      - GITHUB_TOKEN=${GITHUB_TOKEN}
      - CODEX_API_KEY=${CODEX_API_KEY}
      - DEFAULT_BASE_BRANCH=main
    volumes:
      - speckit_workspaces:/work
      - /var/run/docker.sock:/var/run/docker.sock  # Worker launches job containers
    # (optional) securityContext / runAsNonRoot if using rootless Docker

volumes:
  speckit_workspaces:
```

> The worker **does not** carry heavy tools; those live in the **job** image used for each run.

---

## 5) Workspace Layout & HOME

* Named volume: `speckit_workspaces`
* Per run:

  * `/work/runs/{run_id}/home` ← **HOME**
  * `/work/runs/{run_id}/repo`
  * `/work/runs/{run_id}/artifacts`

This guarantees a clean, writable HOME for Codex CLI/Spec Kit and isolates state between runs.

---

## 6) Celery Orchestration (unchanged flow, updated internals)

We keep the **chain** pattern from `001-celery-chain-workflow` and pass an immutable `ctx`:

1. `prepare_job(ctx)`
2. `start_job_container(ctx)` **(new)**
3. `git_clone(ctx)`                 ← runs *inside* job container
4. `run_speckit_phase(ctx, "speckit.specify", input=specify_text)`
5. `run_speckit_phase(ctx, "speckit.plan")`
6. `run_speckit_phase(ctx, "speckit.tasks")`
7. `commit_push_branch(ctx)`        ← inside job container
8. `open_pull_request(ctx)`         ← inside job container
9. `stop_job_container(ctx)` **(new)**
10. `finalize_job(ctx)`

### 6.1 New/updated task summaries

* **`start_job_container(ctx)`**

  * Creates `/work/runs/{run_id}` (on the host via the worker or as the job container’s first step).
  * Runs:
    `docker run --detach --name job-{run_id} -v speckit_workspaces:/work --env-file <(secrets) <job-image> sleep infinity`
  * Stores `container_id` in `ctx`.
  * Exports env for the job container:

    * `HOME=/work/runs/{run_id}/home`
    * `GITHUB_TOKEN`, `CODEX_API_KEY`, `GIT_AUTHOR_*`, `BASE_BRANCH`, etc.

* **`git_clone(ctx)`** (inside the job container)

  ```bash
  mkdir -p "$HOME"
  git config --global user.name  "${GIT_AUTHOR_NAME:-Spec Kit Bot}"
  git config --global user.email "${GIT_AUTHOR_EMAIL:-bot@example.com}"
  git clone --branch "${BASE_BRANCH:-main}" \
    "https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO}.git" /work/runs/${RUN_ID}/repo
  cd /work/runs/${RUN_ID}/repo
  git checkout -b "${BRANCH}"
  ```

* **`run_speckit_phase(ctx, phase_name, input_text=None)`** (inside job container)

  ```bash
  cd /work/runs/${RUN_ID}/repo
  if [ -n "${INPUT_TEXT:-}" ]; then
    codex /prompts:${PHASE} "${INPUT_TEXT}"
  else
    codex /prompts:${PHASE}
  fi
  ```

* **`commit_push_branch(ctx)`** (inside job container)

  ```bash
  cd /work/runs/${RUN_ID}/repo
  if ! git diff --quiet; then
    git add -A
    git commit -m "chore(spec-kit): apply specify/plan/tasks"
    git push --set-upstream origin "${BRANCH}"
    echo "changes_pushed=1" > /work/runs/${RUN_ID}/artifacts/commit_status.env
  else
    echo "changes_pushed=0" > /work/runs/${RUN_ID}/artifacts/commit_status.env
  fi
  ```

* **`open_pull_request(ctx)`** (inside job container)

  ```bash
  cd /work/runs/${RUN_ID}/repo
  if [ "$(grep -o 'changes_pushed=1' /work/runs/${RUN_ID}/artifacts/commit_status.env | wc -l)" -gt 0 ]; then
    gh pr create \
      --base "${BASE_BRANCH:-main}" \
      --head "${BRANCH}" \
      --title "Spec Kit: ${REPO} — ${RUN_ID_SHORT}" \
      --body "Automated changes via speckit.{specify,plan,tasks}" \
      --draft
  fi
  ```

* **`stop_job_container(ctx)`**

  * `docker rm -f job-{run_id}` (always attempt, ignore if already gone)

**Retries, timeouts, and structured logs** remain as in the earlier spec (per-phase `soft_time_limit`, exponential backoff, immutable `ctx`).

---

## 7) Agent Adapter (swappable, unchanged interface)

The orchestration still calls an adapter:

```python
class SpecKitAgent(Protocol):
    def run_prompt(self, prompt_ref: str, input_text: Optional[str], cwd: str, env: Mapping[str, str]) -> CommandResult: ...
```

* **Default**: `CodexCliAgent` runs `codex /prompts:<phase> [input_text]` **inside the job container** (via `docker exec`).
* Future agents: implement the same interface; the worker chooses the adapter via `AGENT_BACKEND`.

---

## 8) Exec Strategy (worker → job container)

Use Docker SDK (preferred) or CLI from the **worker** container:

```python
def exec_in_job(container_id: str, cmd: list[str], cwd: str|None=None, env: dict[str, str]|None=None):
    # Compose: cd + command; pass env; capture stdout/stderr; return exit code
    # (Implementation mirrors existing container_exec utility but targets job container)
```

All phases (`git_clone`, `run_speckit_phase`, PR creation) call `exec_in_job`.

---

## 9) Security Considerations (updated for docker.sock)

* **docker.sock exposure**: grants the worker control over the Docker host. Mitigations:

  * Run the worker on an isolated host/node.
  * Prefer **rootless Docker** if feasible; constrain worker user permissions.
  * Add AppArmor/SELinux profiles; restrict network (optional: user-defined network allowing only GitHub endpoints).
* **Secrets**: inject via env at `docker run` time; mask in logs; never write PAT/API keys to files.
* **GitHub tokens**: prefer GitHub App installation tokens; scope to target repo only.
* **Per-run isolation**: unique HOME & workspace paths inside named volume.
* **Cleanup**: `stop_job_container` + periodic GC for `/work/runs/*` older than TTL.

---

## 10) Observability & Artifacts

* **Logs**: structured fields `{run_id, repo, phase, container_id, branch}`.
* **Artifacts** in `/work/runs/{run_id}/artifacts/`:

  * `phase-<name>.stdout.log`, `phase-<name>.stderr.log`
  * `diff-summary.txt`
  * `commit_status.env`
* **Metrics**: phase durations, exit codes, counts of retries. Monitor via Flower/Prometheus.

---

## 11) Error Handling (unchanged semantics)

| Failure           | Handling                                     |
| ----------------- | -------------------------------------------- |
| Job image missing | Fail fast in `start_job_container`; alert    |
| Clone fails       | Stop chain; attach stderr                    |
| Agent error       | Retry (transient); else stop & attach logs   |
| Push fails        | Retry; fallback branch suffix `-r{n}`        |
| PR create fails   | Retry with backoff; return `pr_pending=true` |
| Timeouts          | Kill job container; mark `phase_timeout`     |
| No changes        | Mark `no_diff=true`; skip PR (default)       |

---

## 12) Testing

* **Unit**: adapters (success/fail/timeout), branch naming, no-diff handling.
* **Integration**: spin a disposable repo; run full chain with a **stub agent** (deterministic outputs).
* **Chaos**: kill job container mid-phase → verify cleanup and idempotent retries.

---

## 13) Rollout

1. Dry-run (no push/PR; capture diffs).
2. Allowlist repos; PRs as **draft** with `spec-kit` labels.
3. Scale concurrency; per-repo rate limit.
4. Add more agents; A/B per language.

---

## 14) Reference Snippets

### 14.1 Start/stop job container (worker side)

```python
import docker, os

def start_job_container(run_id: str, env: dict[str, str]) -> str:
    client = docker.from_env()
    container = client.containers.run(
        image=os.environ.get("JOB_IMAGE", "moon/spec-kit-job:stable"),
        name=f"job-{run_id}",
        command=["sleep", "infinity"],
        environment=env,
        volumes={"speckit_workspaces": {"bind": "/work", "mode": "rw"}},
        detach=True
    )
    return container.id

def stop_job_container(container_id: str):
    client = docker.from_env()
    try:
        client.containers.get(container_id).remove(force=True)
    except Exception:
        pass
```

### 14.2 Branch/PR policy

* **Branch**: `speckit/{YYYYMMDD}/{run_id_short}`
* **Commit**: `chore(spec-kit): apply specify/plan/tasks`
* **PR Title**: `Spec Kit: {repo} — {run_id_short}`
* **Labels**: `spec-kit`, `automation`
* **Draft**: default `true` (configurable)
