Below is a technical plan that fits MoonMind’s current shape (FastAPI + Postgres + Pydantic + existing workflow/automation patterns) and delivers **Option B**:

* **Machine A (Producer)**: a local Codex CLI session (ChatGPT subscription login) queues work into MoonMind.
* **Machine B (Executor)**: a different Codex CLI (also ChatGPT subscription login) runs a small “worker” daemon that **claims jobs**, executes them, and reports results/artifacts back to MoonMind.
* **Queue endpoints are exposed both as**:

  * **REST** (FastAPI routes)
  * **MCP tools** (a tool surface that maps to the same underlying service methods)

I’ll reference repo structure and existing patterns you already have (e.g., routers under `api_service/api/routers/*`, Pydantic schemas in `moonmind/schemas/*`, DB patterns like `SpecWorkflowRepository`, etc.).

---

## 1) Target architecture

### 1.1 High-level flow

```
Machine A (Codex CLI)                 MoonMind API (central)                  Machine B (Codex Worker)
---------------------                 ---------------------                   ------------------------
User + Codex plans work    --->       REST or MCP: queue.enqueue     --->      worker polls/claims job
                                      DB: agent_jobs (queued)                runs codex + tools locally
                                      artifacts storage root                uploads logs/patches/results
User can inspect status    <---       REST: queue.get/list, artifacts <---     marks job complete/fail
```

### 1.2 Why this matches MoonMind’s current design

MoonMind already:

* Uses **FastAPI routers** for task/run APIs (e.g., `api_service/api/routers/workflows.py` + models in `moonmind/schemas/workflow_models.py`).
* Uses **DB repositories** for consistent persistence (e.g., `moonmind/workflows/speckit_celery/repositories.py`).
* Uses **artifact roots** (`settings.spec_workflow.artifacts_root`) and an `ArtifactStorage` utility (`moonmind/workflows/speckit_celery/storage.py`).
* Has “MCP” branding today via `/context` (chat) (`api_service/api/routers/context_protocol.py`). That’s *not* a tool protocol; we’ll add a **tool surface** alongside it.

---

## 2) Core primitive: a first-class Job Queue in Postgres

### 2.1 New DB tables (minimum viable)

Add these tables (either in `api_service/db/models.py` or as a new models module imported into the main metadata like you do for workflow models):

#### `agent_jobs`

* `id` (UUID, PK)
* `type` (string or enum): `codex_exec`, `codex_skill`, `report`, …
* `status` (enum): `queued`, `running`, `succeeded`, `failed`, `cancelled`
* `priority` (int, default 0)
* `payload` (JSONB): job-specific request (repo slug, prompt, skill id, etc.)
* `created_by_user_id` (UUID nullable, like existing runs)
* `requested_by_user_id` (UUID nullable)
* `affinity_key` (string nullable) — stable routing key (optional but good)
* `claimed_by` (string nullable) — worker id
* `lease_expires_at` (timestamp nullable)
* `attempt` (int, default 1)
* `max_attempts` (int default 3)
* `result_summary` (text nullable)
* `error_message` (text nullable)
* `artifacts_path` (string nullable) — path under server artifact root
* timestamps: `created_at`, `updated_at`, `started_at`, `finished_at`

#### `agent_job_events` (optional but very useful)

Append-only events for progress:

* `id` UUID
* `job_id` FK
* `ts`
* `level` (`info|warn|error`)
* `message`
* `payload` JSONB (optional)

#### `agent_job_artifacts` (optional)

If you want artifact metadata like your workflow artifacts:

* `id` UUID
* `job_id` FK
* `name`
* `content_type`
* `size_bytes`
* `digest`
* `storage_path` (relative under job artifact dir)
* timestamps

**MVP can skip `agent_job_events` and `agent_job_artifacts`** and store a minimal artifact list in `agent_jobs.payload` or a JSON field. But you’ll almost certainly want events + artifact metadata soon.

### 2.2 Claiming algorithm (correctness-critical)

Implement “claim job” atomically using a DB transaction:

* Requeue any **expired leases** (running jobs with `lease_expires_at < now()` → set back to `queued` or `failed` depending on attempt policy).
* Select next job:

  * `WHERE status='queued'`
  * Optionally filter by `type` or required capabilities.
  * Order by `priority desc, created_at asc`
  * Use `FOR UPDATE SKIP LOCKED LIMIT 1` (Postgres)
* Update it to `running`, set `claimed_by`, `lease_expires_at = now()+leaseSeconds`, `started_at` if first start.

This is the same concurrency shape you *implicitly* rely on for Celery workers, but now explicit for remote worker polling.

### 2.3 Repository + service layer (shared by REST and MCP)

Follow the pattern in `moonmind/workflows/speckit_celery/repositories.py`:

* `moonmind/workflows/agent_queue/repositories.py`

  * `create_job(...)`
  * `claim_job(...)`
  * `heartbeat(job_id, worker_id, extend_seconds)`
  * `complete_job(job_id, worker_id, result, artifacts)`
  * `fail_job(job_id, worker_id, error, retryable)`
  * `append_event(...)`
  * `register_artifact(...)`
* `moonmind/workflows/agent_queue/service.py` (optional but recommended)

  * enforce status transitions
  * enforce worker ownership / lease checks
  * implement retry rules

---

## 3) REST API: queue endpoints

### 3.1 Where it lives

Add a new router:

* `api_service/api/routers/agent_queue.py`
  and include it in `api_service/main.py` like other routers.

Prefix suggestion:

* `/api/queue` (generic)
  or
* `/api/agent-jobs` (explicit)

### 3.2 REST endpoints (MVP set)

**Create**

* `POST /api/queue/jobs`

  * body: `CreateJobRequest`
  * returns: `JobModel` (or `CreateJobResponse`)

**Claim**

* `POST /api/queue/jobs/claim`

  * body: `ClaimJobRequest { workerId, leaseSeconds, allowedTypes? }`
  * returns: `ClaimJobResponse { job: JobModel | null }`

**Heartbeat**

* `POST /api/queue/jobs/{jobId}/heartbeat`

  * body: `{ workerId, leaseSeconds }`

**Complete**

* `POST /api/queue/jobs/{jobId}/complete`

  * body: `{ workerId, resultSummary, outputs?, artifacts? }`

**Fail**

* `POST /api/queue/jobs/{jobId}/fail`

  * body: `{ workerId, errorMessage, retryable, artifacts? }`

**Inspect**

* `GET /api/queue/jobs/{jobId}`
* `GET /api/queue/jobs?status=&type=&limit=`

### 3.3 Auth wiring

Reuse existing auth dependency patterns:

* For local/dev where `AUTH_PROVIDER=disabled`, `get_current_user()` returns the default user without token enforcement (see `api_service/auth_providers.py`).
* For production, require a real user or a dedicated worker identity:

  * simplest: **JWT/OIDC** auth like your other routers (`Depends(get_current_user())`)
  * better: a **worker token** (see §7)

---

## 4) Artifact ingestion: required for cross-machine execution

Right now, workflows assume artifacts are written on the same filesystem (`var/artifacts/...`) and served back via file responses (e.g., `api_service/api/routers/spec_automation.py` downloads from `settings.spec_workflow.artifacts_root`).

For Option B, the executor is remote, so you need an upload path.

### 4.1 Add upload endpoint(s)

Add to `agent_queue` router:

* `POST /api/queue/jobs/{jobId}/artifacts/upload`

  * multipart form:

    * `file`
    * `name` (relative path under job)
    * `contentType` (optional)
    * `digest` (optional)
  * server stores it under:

    * `${ARTIFACT_ROOT}/agent_jobs/<jobId>/<name>`
  * records metadata in `agent_job_artifacts` (or in job JSON)

Also add:

* `GET /api/queue/jobs/{jobId}/artifacts`
* `GET /api/queue/jobs/{jobId}/artifacts/{artifactId}/download`

Implement storage with your existing `ArtifactStorage` pattern (`moonmind/workflows/speckit_celery/storage.py`) but:

* allow per-job directory roots
* enforce path traversal protections (you already do this in `ArtifactStorage.get_run_path()`)

### 4.2 Artifact root config

Introduce a new setting for agent-job artifacts to avoid mixing with spec workflows:

* `AGENT_JOB_ARTIFACT_ROOT` default: `var/artifacts/agent_jobs`

---

## 5) MCP tool surface: same operations as REST

MoonMind currently calls `/context` “Model Context Protocol” for chat (`api_service/api/routers/context_protocol.py`). That’s fine for OpenHands-style “send messages to a model,” but you need a **tool protocol surface** for queue operations.

### 5.1 Goal

Expose queue as MCP tools like:

* `queue.enqueue`
* `queue.claim`
* `queue.heartbeat`
* `queue.complete`
* `queue.fail`
* `queue.get`
* `queue.list`
* `queue.upload_artifact` (optional)

### 5.2 Implementation approach (recommended)

Implement **one internal service interface** and two thin adapters:

* REST router calls service methods
* MCP router calls the *same* service methods

This keeps behavior identical.

### 5.3 MCP server shape

You have two pragmatic choices:

#### Choice A (MVP / pragmatic): “MCP-as-tools-over-HTTP”

Implement an MCP-ish tool router with:

* `GET /mcp/tools` → list tool definitions (name, description, JSON schema)
* `POST /mcp/tools/call` → `{ tool, arguments }` → `{ result }`

Codex CLI (and other agents) can treat this as a tools backend via a small adapter (or you write a tiny MCP client wrapper on the Codex side). This is fastest and avoids taking a dependency on a specific MCP SDK.

#### Choice B (more standard): implement MCP JSON-RPC tool protocol

Implement JSON-RPC methods:

* `initialize`
* `tools/list`
* `tools/call`

Expose over HTTP (or SSE) from FastAPI.

**Plan-wise:** I’d start with A to get real value fast, then conform to B once you decide which MCP clients you’re targeting (Codex CLI vs OpenHands vs Claude Desktop etc.).

### 5.4 Where to put MCP tool code

Add:

* `api_service/api/routers/mcp_tools.py`
* `moonmind/mcp/tool_registry.py`

  * registers tool definitions (names + arg schemas)
  * maps tool calls to service methods
* `moonmind/schemas/agent_queue_models.py`

  * shared Pydantic models used by both REST and MCP

---

## 6) The remote executor: “moonmind-codex-worker” (Machine B)

### 6.1 Worker responsibilities

A simple daemon that:

1. **Claims** a job (REST or MCP)
2. Executes it locally using **Codex CLI** + optional tool CLIs (git/gh/etc)
3. Uploads artifacts back to MoonMind
4. Marks the job **complete or failed**
5. Maintains lease heartbeats

### 6.2 Packaging (repo changes)

Add a worker module and CLI entrypoint:

* `moonmind/agents/codex_worker/worker.py`
* `moonmind/agents/codex_worker/handlers.py`
* `moonmind/agents/codex_worker/cli.py`
* Add `poetry` script entry:

  * `moonmind-codex-worker = "moonmind.agents.codex_worker.cli:main"`

This mirrors how you already centralize worker bootstrap (e.g., `celery_worker/speckit_worker.py`) but runs outside Celery.

### 6.3 Worker config

Environment variables:

* `MOONMIND_URL` (e.g., `http://moonmind-host:5000`)
* `MOONMIND_WORKER_ID` (e.g., hostname)
* `MOONMIND_WORKER_TOKEN` (if auth enabled)
* `MOONMIND_POLL_INTERVAL_MS` (default 1500)
* `MOONMIND_LEASE_SECONDS` (default 120)
* `MOONMIND_WORKDIR` (local base dir for repo checkouts)
* `MOONMIND_CODEX_MODEL` (optional worker default model; falls back to `CODEX_MODEL`)
* `MOONMIND_CODEX_EFFORT` (optional worker default effort; falls back to `CODEX_MODEL_REASONING_EFFORT`)
* `GITHUB_TOKEN` (optional for pushing PRs)
* `CODEX_*` optional (if you want to force model/env)

Worker-level Codex defaults are allowed, but per-task payload overrides should take precedence for that task.

On startup:

* verify `codex` exists (`verify_cli_is_executable("codex")` in `moonmind/workflows/speckit_celery/utils.py`)
* run `codex login status` and fail fast if not authenticated (same spirit as your preflight check in `moonmind/workflows/speckit_celery/tasks.py`)

### 6.4 Job types (MVP)

Start with two:

#### `codex_exec`

Payload example:

```json
{
  "repository": "MoonLadderStudios/MoonMind",
  "ref": "main",
  "workdirMode": "fresh_clone",
  "instruction": "Implement X; run tests; produce a patch + summary",
  "codex": {
    "model": "gpt-5-codex",
    "effort": "high"
  },
  "publish": { "mode": "none|branch|pr", "baseBranch": "main" }
}
```

Override contract:

* `codex.model` (optional)
* `codex.effort` (optional)
* precedence: `payload.codex.*` -> worker defaults -> Codex CLI defaults
* scope: applies only to the claimed task

Worker steps:

1. checkout repo
2. run `codex exec` in repo with resolved model/effort for this task
3. capture stdout/stderr to log file
4. `git diff > patch`
5. optionally `gh pr create`
6. upload artifacts
7. `complete_job(...)`

#### `codex_skill`

Payload example:

```json
{
  "skillId": "vertical_slice_gap_report",
  "codex": {
    "model": "gpt-5-codex",
    "effort": "medium"
  },
  "inputs": { "repo": "MoonLadderStudios/Tactics", "sliceDocId": "..." }
}
```

Worker steps:

1. fetch skill definition from MoonMind (REST/MCP)
2. execute its steps with resolved Codex model/effort for this task
3. upload report artifact
4. enqueue follow-up jobs (via REST/MCP from worker)

### 6.5 Progress + robustness

* Heartbeat every `leaseSeconds/3`
* Write local log incrementally
* Periodically upload “rolling log” artifact (optional)
* On crash: lease expires → job becomes reclaimable

---

## 7) Security & auth (so it works across machines safely)

### 7.1 MVP security posture

If you run MoonMind on a private LAN and `AUTH_PROVIDER=disabled`, endpoints are effectively unauthenticated (consistent with how `get_current_user()` falls back to default user). That’s OK for LAN MVP.

### 7.2 Production posture (recommended)

Add a “worker auth” mechanism:

**Option 1: OIDC/JWT**

* Worker gets a token like any other client and uses `Authorization: Bearer <token>`.

**Option 2: Dedicated worker tokens**

* Add table: `worker_tokens`
* Admin creates token(s) in UI/CLI
* Worker sends `X-MoonMind-Worker-Token: ...`
* Server validates and maps to a worker identity + permissions (allowed repos, allowed job types)

Also enforce:

* repository allowlist (you already do similar checks in `spec_automation.py` for run access)
* job type allowlist per worker token
* max artifact sizes + content type restrictions

---

## 8) “Producer” side (Machine A / Codex CLI session)

### 8.1 How producer enqueues

Provide both:

* REST: `POST /api/queue/jobs`
* MCP tool: `queue.enqueue(...)`

For convenience, ship a small CLI:

* `moonmind-queue enqueue --type codex_exec --repo ... --instruction ...`

Then the user can:

* manually run the CLI
* or ask Codex to run it (Codex can call shell commands)

### 8.2 Minimal UX loop

1. In Codex CLI (machine A), design the work
2. Call `moonmind-queue enqueue …` (or MCP tool)
3. Watch status in MoonMind UI or via `GET /api/queue/jobs?...`
4. Executor finishes, artifacts available centrally

---

## 9) Implementation steps (milestones)

### Milestone 1 — DB + REST queue (MVP queue)

* Add `agent_jobs` table + Alembic migration
* Add repository + service methods
* Add REST router:

  * enqueue / claim / heartbeat / complete / fail / get / list
* Basic unit tests (DB transitions + claim concurrency with SKIP LOCKED)

### Milestone 2 — Artifact upload (required for cross-machine)

* Add artifact storage root + upload endpoint
* Add artifact metadata table (or embed in job JSON for MVP)
* Add download/list endpoints
* Test traversal defenses and size limits

### Milestone 3 — Remote worker daemon

* Add `moonmind-codex-worker` CLI
* Implement `codex_exec` handler end-to-end:

  * clone repo
  * `codex login status`
  * `codex exec`
  * `git diff` patch
  * upload artifacts
  * complete job
* Add lease renewal + crash recovery behavior

### Milestone 4 — MCP tools wrapper

* Add MCP tool router + tool registry
* Expose queue operations as tools calling the same service methods
* Provide a tiny “how to configure Codex to call MoonMind tools” doc (even if initially via HTTP tool adapter)

### Milestone 5 — Hardening + quality

* Worker tokens / OIDC enforcement
* Repo allowlist per worker
* job capability matching
* job events + streaming-ish logs
* retries + backoff + dead-letter behavior

---

## 10) Concrete API + MCP shapes (suggested)

### REST (example)

```http
POST /api/queue/jobs
{
  "type": "codex_exec",
  "priority": 10,
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "instruction": "Run tests and summarize failures",
    "codex": {
      "model": "gpt-5-codex",
      "effort": "high"
    }
  }
}
```

```http
POST /api/queue/jobs/claim
{
  "workerId": "executor-01",
  "leaseSeconds": 120,
  "allowedTypes": ["codex_exec","codex_skill"],
  "workerCapabilities": ["codex","git","gh"]
}
```

Worker-authenticated claim/mutation requests can use:

- `X-MoonMind-Worker-Token: <token>` for dedicated worker tokens
- `Authorization: Bearer <jwt>` for OIDC/JWT flows

Incremental progress polling surface:

- `POST /api/queue/jobs/{jobId}/events` (append lifecycle/log event)
- `GET /api/queue/jobs/{jobId}/events?after=<iso8601>&limit=<n>` (streaming-ish poll)

### MCP tools (example conceptual)

```json
{
  "tool": "queue.enqueue",
  "arguments": {
    "type": "codex_exec",
    "priority": 10,
    "payload": {
      "repository": "MoonLadderStudios/MoonMind",
      "instruction": "Run tests and summarize failures",
      "codex": {"model": "gpt-5-codex", "effort": "high"}
    }
  }
}
```

```json
{ "tool": "queue.claim", "arguments": { "workerId":"executor-01", "leaseSeconds":120 } }
```

The MCP adapter should return the same `JobModel` you return from REST.

---

## 11) Repo change list (where to implement)

**New**

* `moonmind/schemas/agent_queue_models.py`
* `moonmind/workflows/agent_queue/repositories.py`
* `moonmind/workflows/agent_queue/service.py`
* `api_service/api/routers/agent_queue.py`
* `api_service/api/routers/mcp_tools.py` (tool surface)
* `moonmind/mcp/tool_registry.py`
* `moonmind/agents/codex_worker/*`
* Alembic migration for `agent_jobs` (+ artifacts/events tables if included)

**Modified**

* `api_service/main.py` (include new router(s))
* `pyproject.toml` (add CLI scripts)
* `docs/` (worker setup + how-to enqueue + how-to run executor)
