# Spec Kit Automation — Technical Design Document 

**Status:** Draft (proposed)
**Owners:** MoonMind Eng
**Last updated:** Nov 3, 2025
**Related prior art:** `specs\001-celery-chain-workflow` in the MoonMind repo (Celery chain best practices & context propagation)

---

## 1) Goal & Scope

Design and implement an automated pipeline that:

1. Accepts a **Celery** task with:

   * `specify_text` (free‑form GitHub Spec Kit “specify” input)
   * `repo` (GitHub repository URL or `owner/name`)
   * optional tuning inputs (branch, labels, draft PR, agent backend, timeouts)

2. Spins up an **ephemeral Docker container**,

   * clones the target repo,
   * prepares the environment (`HOME`, credentials, Git config) required for **Codex CLI** and **GitHub Spec Kit**.

3. Executes **GitHub Spec Kit** prompts *sequentially* using **Celery Chain**:

   * `/prompts:speckit.specify <specify_text>`
   * `/prompts:speckit.plan`
   * `/prompts:speckit.tasks`

4. Commits the resulting changes on a new branch and **opens a GitHub Pull Request**.

5. Keeps the “agent” backend **swappable** (Codex CLI by default; can be replaced with any Spec Kit–compatible agent).

**Out of scope (for this iteration):**

* Multi-repo orchestration
* Fine-grained policy approval gates
* Long-lived container workers or Kubernetes operators (we’ll add later if needed)

---

## 2) Key Requirements

* **Deterministic sequencing:** `specify → plan → tasks` run in order, each phase only after the previous phase completes successfully.
* **Isolation:** Each job runs in a fresh container with no cross-run state leakage.
* **Pluggable agent:** Codex CLI is the default adapter, but the “agent” execution is an interface; swapping requires **no** changes to orchestration logic.
* **Observability:** Structured logs, task IDs, and artifact capture (stdout/stderr, prompt inputs/outputs).
* **Idempotency:** Safe re-runs; unique branch naming; gracefully handles “no changes” runs.
* **Security:** Secrets never written to disk in plaintext; bounded network access; minimal scopes on tokens.
* **Scalability:** Horizontal Celery worker scale; bounded concurrency per queue.
* **Time-bounded:** Per-phase timeouts; container hard-kills on timeout.

---

## 3) Architecture Overview

### 3.1 Components

* **API submitter** (can be a Celery producer, HTTP endpoint, or internal CLI):

  * Builds a **context object** and enqueues a single **kickoff** Celery task.

* **Celery Orchestrator**:

  * Uses **Celery Chain** to run tasks sequentially:

    1. `prepare_job`
    2. `git_clone_in_container`
    3. `run_speckit_phase(specify)`
    4. `run_speckit_phase(plan)`
    5. `run_speckit_phase(tasks)`
    6. `commit_push_branch`
    7. `open_pull_request`
    8. `finalize_job`

* **Docker Runner**:

  * Spawns ephemeral containers with the Spec Kit toolchain.
  * Mounts a per-run workspace.
  * Sets `HOME` to a workspace path compatible with **Codex CLI**.

* **Agent Adapter Layer**:

  * Abstract interface providing `run_prompt(prompt_name, input_text | None, cwd, env)`.
  * Default implementation: **CodexCliAgent** (executes the Codex CLI).
  * Future implementations: any Spec Kit–compatible agent.

* **GitHub Integration**:

  * Commit, push, and PR creation via **`gh` CLI** or REST API (token auth).

* **Artifact Store** (optional for this iteration):

  * Persist logs and outputs per run (local disk, S3, or DB).

### 3.2 Sequence Diagram

```mermaid
sequenceDiagram
  autonumber
  participant Producer as Producer / API
  participant Celery as Celery Broker
  participant Worker as Celery Worker
  participant Docker as Docker Engine
  participant C as Ephemeral Container
  participant GH as GitHub

  Producer->>Celery: enqueue kickoff(specify_text, repo, opts)
  Celery->>Worker: deliver task
  Worker->>Docker: run container(image, env, mounts)
  Docker-->>Worker: container id
  Worker->>C: git clone repo; set HOME; gh auth
  Worker->>C: agent.run('/prompts:speckit.specify', specify_text)
  C-->>Worker: outputs + exit code
  Worker->>C: agent.run('/prompts:speckit.plan')
  Worker->>C: agent.run('/prompts:speckit.tasks')
  Worker->>C: git add/commit/push new branch
  Worker->>GH: create PR (title/body/labels/draft)
  GH-->>Worker: PR URL
  Worker-->>Producer: result {run_id, pr_url, logs}
  Worker->>Docker: stop & remove container
```

---

## 4) Build on `specs\001-celery-chain-workflow` Practices

We inherit the following patterns from `specs\001-celery-chain-workflow`:

* **Context-first chaining:** a single context dict is passed immutably across tasks (additive updates only).
* **Immutable signatures:** Celery `sig(...).set(immutable=True)` to avoid argument clobbering.
* **Structured logging:** `{run_id, task_name, repo, phase, container_id}` fields on every log line.
* **Retries & acks_late:** configure idempotent retries and `acks_late=True` where safe.
* **Timeouts:** `soft_time_limit` and `time_limit` per task; container kill after `time_limit`.
* **Result envelope:** each task returns `{ok, data, error?, metrics}`.

---

## 5) Container Image

**Base image:** Minimal Linux with:

* `git`, `gh` (GitHub CLI), `bash`, `curl`, `jq`
* **Codex CLI** (default agent) and Spec Kit prompts preinstalled
* Python (3.11+) if needed by prompts
* Optional build tools needed by repos (node, pnpm, go, etc.) — *keep image slim; add variants per language if needed*

```Dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    git gh curl jq bash ca-certificates python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

# Install Codex CLI (placeholder; replace with official install)
# RUN curl -fsSL https://.../install-codex.sh | bash

# Add prompts/speckit bundle if not fetched at runtime
# COPY prompts/ /usr/local/share/spec-kit/prompts/

ENV PATH="/usr/local/bin:${PATH}"
```

> **Note:** Keep Codex CLI and Spec Kit versions pinned. Expose build ARGs to pin versions for reproducibility.

---

## 6) Configuration & Secrets

| Variable                               | Purpose                          | Example                              |
| -------------------------------------- | -------------------------------- | ------------------------------------ |
| `GITHUB_TOKEN`                         | Auth for `git`/`gh` push & PR    | fine‑scoped PAT or GitHub App token  |
| `AGENT_BACKEND`                        | `codex` (default) or other       | `codex`                              |
| `CODEX_API_KEY`                        | If Codex CLI needs a key         | secret                               |
| `HOME`                                 | Required by Codex CLI & Spec Kit | `/workspace/home` (inside container) |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | Commit identity                  | `Spec Kit Bot` / `bot@domain`        |
| `DEFAULT_BASE_BRANCH`                  | Base for PRs                     | `main`                               |
| `CELERY_BROKER_URL`                    | Broker                           | `redis://redis:6379/0`               |
| `CELERY_RESULT_BACKEND`                | Results                          | `redis://redis:6379/1`               |

**Secrets handling**

* Inject via **Docker env** at run time, not baked into images.
* Mask tokens in logs; never echo command lines with tokens.
* For `git push`, prefer HTTPS URL with token or `gh auth login --with-token`.

---

## 7) Task Orchestration (Celery)

### 7.1 Public Entry Point

```python
# tasks/api.py
from celery import chain, signature as sig
from .workflow import (
    prepare_job, git_clone_in_container, run_speckit_phase,
    commit_push_branch, open_pull_request, finalize_job
)

def submit_spec_kit_job(specify_text: str, repo: str, *, branch: str|None=None,
                        agent_backend: str="codex", draft_pr: bool=True,
                        labels: list[str]|None=None, base_branch: str="main"):
    ctx = {
        "run_id": new_run_id(),  # e.g., ULID
        "repo": normalize_repo(repo),  # owner/name
        "specify_text": specify_text,
        "agent_backend": agent_backend,
        "branch": branch,  # if None, derived later
        "base_branch": base_branch,
        "draft_pr": draft_pr,
        "labels": labels or ["spec-kit"],
        "timeouts": {"phase": 60*20, "total": 60*60},
    }

    flow = chain(
        sig(prepare_job, (ctx,), immutable=True),
        sig(git_clone_in_container, immutable=True),
        sig(run_speckit_phase, ("speckit.specify",), immutable=True),
        sig(run_speckit_phase, ("speckit.plan",), immutable=True),
        sig(run_speckit_phase, ("speckit.tasks",), immutable=True),
        sig(commit_push_branch, immutable=True),
        sig(open_pull_request, immutable=True),
        sig(finalize_job, immutable=True),
    )
    return flow.apply_async()
```

> **Pattern from `001-celery-chain-workflow`:** single `ctx` threaded through immutable signatures, updated by tasks (never replaced).

### 7.2 Core Tasks (summaries)

* `prepare_job(ctx)`

  * Generate `branch` if unset: `speckit/{YYYYMMDD}/{short-run-id}`
  * Decide container image & resource limits based on repo language (optional)
  * Return `ctx + {"branch", "image", "limits"}`

* `git_clone_in_container(ctx)`

  * Create workspace on host: `/var/run/spec-kit/{run_id}`
  * `docker run` container (auto-remove off; we need it across phases) with:

    * volume mount: `workspace:/workspace`
    * env: `HOME=/workspace/home`, `GITHUB_TOKEN`, `GIT_*`
  * Inside container:

    * `mkdir -p $HOME && git config --global user.name "$GIT_AUTHOR_NAME"`
    * `git clone --branch {base_branch} https://x-access-token:${GITHUB_TOKEN}@github.com/{repo}.git /workspace/repo`
    * `cd /workspace/repo && git checkout -b {branch}`
    * `gh auth setup-git` (optional) or rely on token URL
  * Save `container_id`, `workspace`, `repo_path` into `ctx`.

* `run_speckit_phase(ctx, phase_name)`

  * Prepare input: for `speckit.specify`, pass `specify_text`; for others, no explicit text.
  * Delegate to **Agent Adapter**:

    * `adapter.run_prompt(f"/prompts:{phase_name}", input_text, cwd="/workspace/repo", env=...)`
  * Capture stdout/stderr & exit code; store artifacts.

* `commit_push_branch(ctx)`

  * Inside container:

    * `cd /workspace/repo`
    * `if git status --porcelain | grep .; then git add -A && git commit -m "Spec Kit: {phase summary}" ; fi`
    * `git push --set-upstream origin {branch}`
  * If **no changes**, mark outcome accordingly and skip PR or open a no-op PR based on policy flag `allow_empty_pr`.

* `open_pull_request(ctx)`

  * `gh pr create --base {base_branch} --head {branch} --title "...“ --body "...“ {--draft}`
  * Add labels: `gh pr edit --add-label ...` (or REST API)
  * Record `pr_url` in `ctx`.

* `finalize_job(ctx)`

  * Stop & remove the container.
  * Emit final result envelope: `{run_id, pr_url?, artifacts, metrics}`.

**Retries & timeouts**

* Each task: `soft_time_limit=phase_timeout`, `max_retries=2`, `retry_backoff=True`.
* `run_speckit_phase`: on `SoftTimeLimitExceeded`, kill the phase process in the container; on hard limit, kill the container & mark failure.

---

## 8) Agent Adapter (Pluggable “AI Agent”)

Define an interface to decouple orchestration from the agent implementation:

```python
# agents/base.py
from typing import Protocol, Optional, Mapping

class CommandResult(TypedDict):
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int

class SpecKitAgent(Protocol):
    name: str
    def run_prompt(
        self,
        prompt_ref: str,              # e.g., "/prompts:speckit.plan"
        input_text: Optional[str],    # used for 'specify'; None for others
        cwd: str,                     # repo path inside container
        env: Mapping[str, str],       # includes HOME, tokens
    ) -> CommandResult: ...
```

**Default implementation: Codex CLI**

```python
# agents/codex_cli.py
import shlex, subprocess, time

class CodexCliAgent:
    name = "codex"

    def run_prompt(self, prompt_ref, input_text, cwd, env):
        cmd = ["codex", prompt_ref]
        if input_text:
            cmd.append(input_text)  # matches required form: /prompts:speckit.specify <specify text>

        t0 = time.time()
        proc = subprocess.run(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "elapsed_ms": int((time.time()-t0)*1000),
        }
```

> **Swap policy:** Choose backend via `AGENT_BACKEND` (env or per-run option). Future adapters (e.g., `OpenAIAgent`, `CustomAgent`) implement the same interface and are selected at runtime with a small factory.

---

## 9) Running Commands Inside the Container

A lightweight “exec” layer uses the Docker Engine API to execute in the existing container:

```python
# infra/container_exec.py
import docker, json

def exec_in_container(container_id: str, cmd: list[str], env: dict[str,str]|None=None, cwd: str|None=None) -> dict:
    client = docker.from_env()
    # Compose a shell command that cd's then runs the cmd
    sh = ["bash", "-lc", ("cd {cwd} && ".format(cwd=cwd) if cwd else "") + " ".join(map(shlex.quote, cmd))]
    exec_id = client.api.exec_create(container_id, sh, environment=env, workdir=cwd, stdout=True, stderr=True)
    out = client.api.exec_start(exec_id, stream=False, demux=True)
    inspect = client.api.exec_inspect(exec_id)
    stdout, stderr = out if isinstance(out, tuple) else (out, b"")
    return {"exit_code": inspect["ExitCode"], "stdout": stdout.decode(), "stderr": stderr.decode()}
```

**Environment inside container:**

* `HOME=/workspace/home`
* `GITHUB_TOKEN` (scoped to repo)
* `CODEX_API_KEY` (if Codex requires)
* `PATH` includes Codex CLI & `gh`

---

## 10) Pull Request Strategy

* **Branch name:** `speckit/{YYYYMMDD}/{run_id_short}`
* **Commit messages:**

  * `chore(spec-kit): apply 'specify' / 'plan' / 'tasks' changes`
* **PR title:** `Spec Kit: {repo} — {run_id_short}`
* **PR body:**

  * Summarize outputs from each phase (truncate or attach as artifact)
* **Labels:** `spec-kit`, `automation`
* **Draft vs Ready:** default **draft**; expose flag to open as ready.

**No-change runs**

* If no diff after `tasks`, skip PR (default). Optionally open a PR with “no changes” note if `allow_empty_pr=true`.

---

## 11) Observability & Artifacts

* **Logging fields:** `run_id`, `repo`, `phase`, `container_id`, `branch`
* **Metrics:** phase duration, token usage (if known), exit codes
* **Artifacts per phase:**

  * Raw stdout/stderr
  * Prompt input snapshot (sanitized)
  * Diff summary after `tasks`
* **Dashboards:** Flower for Celery; optional Prometheus counters/gauges.

---

## 12) Error Handling Matrix

| Failure                         | Action                                                           |
| ------------------------------- | ---------------------------------------------------------------- |
| Docker daemon not reachable     | Fail `prepare_job`; retry up to N; alert                         |
| Clone fails                     | Abort chain; mark as `git_error`; include stderr                 |
| Agent returns non‑zero          | Retry (if transient); else stop and attach logs                  |
| Commit/push fails               | Retry with exponential backoff; if branch exists, append `-r{n}` |
| PR creation fails (rate limits) | Retry with backoff; if persists, return `pr_pending=true`        |
| Timeouts                        | Kill container; mark `phase_timeout`; include partial logs       |
| No changes                      | Return `no_diff=true`; optionally skip PR                        |

---

## 13) Security Considerations

* **Token scope:** Minimal repo access; prefer ephemeral GitHub App installation tokens.
* **Secret hygiene:** Env only; masked logs; never written to workspace.
* **FS isolation:** Per-run workspace; container user may be non‑root.
* **Network:** Optionally restrict outbound traffic; allow GitHub domains only.
* **Supply chain:** Pin versions & verify checksums of Codex CLI; verified `gh` releases.

---

## 14) Local Development & Ops

### 14.1 `docker-compose.yml` (starter)

```yaml
version: "3.9"
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  worker:
    build: .
    command: celery -A app.celery_app worker --loglevel=INFO --concurrency=2 -Q speckit
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - CELERY_RESULT_BACKEND=redis://redis:6379/1
      - AGENT_BACKEND=codex
      - GITHUB_TOKEN=${GITHUB_TOKEN}
      - CODEX_API_KEY=${CODEX_API_KEY}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock  # worker controls Docker
      - ./runtime:/var/run/spec-kit

  flower:
    image: mher/flower
    command: flower --broker=redis://redis:6379/0
    ports: ["5555:5555"]
```

> The worker mounts the Docker socket to orchestrate ephemeral containers per run.

### 14.2 Example: Submit a Job (Python)

```python
from tasks.api import submit_spec_kit_job

res = submit_spec_kit_job(
    specify_text="Implement OAuth2 login route and tests",
    repo="moon-org/moonmind",
    agent_backend="codex",
    draft_pr=True,
    labels=["spec-kit","automation"]
)
print("Enqueued:", res.id)
```

---

## 15) Testing Strategy

* **Unit tests**

  * Agent adapters: simulate success/failure/timeout
  * Git layer: branch naming, “no changes” detection
  * Context mutation invariants

* **Integration tests**

  * Spin ephemeral private test repo
  * Full chain execution against a stubbed Codex CLI (deterministic)
  * Assert PR created with expected title/body

* **Chaos**

  * Kill container mid‑phase to validate cleanup
  * Induce Git push conflicts

---

## 16) Rollout Plan

1. **Phase 0**: Dry‑run mode (no push/PR; collect diffs).
2. **Phase 1**: Limited repos allowlist; PRs as draft with “automation” label.
3. **Phase 2**: Scale workers; enable concurrency controls (per-repo limits).
4. **Phase 3**: Add more agent adapters; A/B agent selection by repo language.

---

## 17) Appendix

### 17.1 Concrete Commands Run Inside the Container

```bash
# Assumes env: HOME=/workspace/home, GITHUB_TOKEN, GIT_AUTHOR_NAME/EMAIL

mkdir -p "$HOME"
git config --global user.name  "${GIT_AUTHOR_NAME:-Spec Kit Bot}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-bot@example.com}"

git clone --branch "${BASE_BRANCH:-main}" \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO}.git" /workspace/repo
cd /workspace/repo

git checkout -b "${BRANCH}"

# 1) specify
codex /prompts:speckit.specify "<SPECIFY_TEXT>"

# 2) plan
codex /prompts:speckit.plan

# 3) tasks
codex /prompts:speckit.tasks

# Commit & push if changed
if ! git diff --quiet; then
  git add -A
  git commit -m "chore(spec-kit): apply specify/plan/tasks"
  git push --set-upstream origin "${BRANCH}"
fi

# Create PR (draft by default)
gh pr create \
  --base "${BASE_BRANCH:-main}" \
  --head "${BRANCH}" \
  --title "Spec Kit: ${REPO} — ${RUN_ID}" \
  --body "Automated changes via speckit.{specify,plan,tasks}" \
  --draft
```

### 17.2 Example Data Contract (Result Envelope)

```json
{
  "run_id": "01J...Z6",
  "repo": "owner/name",
  "branch": "speckit/20251103/01JZ6",
  "phases": [
    {"name": "speckit.specify", "ok": true, "elapsed_ms": 45231},
    {"name": "speckit.plan", "ok": true, "elapsed_ms": 19874},
    {"name": "speckit.tasks", "ok": true, "elapsed_ms": 80123}
  ],
  "diff_summary": { "files_changed": 7, "insertions": 185, "deletions": 22 },
  "pr_url": "https://github.com/owner/name/pull/123",
  "no_diff": false
}
```

---

## 18) Implementation Notes & Assumptions

* **HOME variable:** Codex CLI and Spec Kit expect writable `$HOME` for caches/config. We set `HOME=/workspace/home` (mounted tmpfs or host volume) to isolate runs and avoid permission issues.
* **Agent portability:** Agents must accept the same `prompt_ref` naming and run inside the same working tree (`/workspace/repo`). If an agent differs, the adapter translates to its native invocation.
* **GitHub auth:** Prefer GitHub App tokens for auditable writes; fallback to PAT for simplicity in early phases.
* **Workspace cleanup:** Always remove container; garbage-collect old workspaces on a schedule.

---

### TL;DR

We orchestrate a **Celery chain** that spins up an **ephemeral container**, runs **Spec Kit** phases (`specify → plan → tasks`) through a **pluggable agent adapter** (default **Codex CLI**), then **commits and opens a PR**. The design leans on `specs\001-celery-chain-workflow` patterns (immutable context, explicit timeouts, structured logs), and cleanly separates orchestration from the agent to keep Codex CLI swappable without touching the workflow.
