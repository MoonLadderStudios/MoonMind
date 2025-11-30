# Codex CLI Worker Architecture

This document describes how MoonMind uses a **Codex-focused Celery worker group** to execute Spec Kit automation phases that depend on the Codex CLI, how the **`codex` queue** is used for routing, and how a **named persistent volume** is shared across containers to preserve OAuth authentication.

---

## 1. Goals

The Codex worker group exists to support the Celery chain that drives Spec Kit workflows end-to-end:

- Discover the next actionable Spec phase.
- Submit work to Codex Cloud.
- Poll for diffs / patches.
- Apply changes and publish branches/PRs.
- Persist artifacts and emit structured status back to MoonMind’s UI.:contentReference[oaicite:3]{index=3}

The key goals from the specs:

- **Dedicated Codex queue**: All Codex-heavy tasks are isolated on a named `codex` queue, separate from default Celery traffic.:contentReference[oaicite:4]{index=4}
- **Persistent OAuth**: Codex CLI runs reuse a **persistent sign-in** via a named auth volume so automation never stalls on interactive login.:contentReference[oaicite:5]{index=5}
- **Containerized tooling**: The api_service / worker image includes Codex CLI and GitHub Spec Kit CLI, plus a managed `.codex/config.toml` with `approval_policy = "never"` for non-interactive approvals.:contentReference[oaicite:6]{index=6}:contentReference[oaicite:7]{index=7}

---

## 2. Celery Topology and the `codex` Queue

### 2.1 Celery Chain Overview

The Spec Kit integration composes a **Celery Chain** with discrete tasks for: discovery, submission, apply/poll, PR creation, and finalization.:contentReference[oaicite:8]{index=8} Each `SpecWorkflowRun` tracks the chain ID, per-task status, artifacts (Codex logs, patches), and timestamps.:contentReference[oaicite:9]{index=9}

High-level tasks:

- `discover_next_phase` – parses Spec Kit tasks and returns the next phase or a “no work” signal.:contentReference[oaicite:10]{index=10}
- `submit_to_codex` – invokes Codex Cloud, captures the task ID, and persists streamed JSONL logs.:contentReference[oaicite:11]{index=11}
- `poll_and_apply_diff` – polls Codex for diffs, applies the patch, and captures conflict artifacts.:contentReference[oaicite:12]{index=12}
- `publish_pr` – ensures a branch exists, pushes commits, and creates or updates a PR via GitHub.:contentReference[oaicite:13]{index=13}
- `finalize_run` – records artifacts and run outcome.

### 2.2 `codex` Queue

A dedicated **Celery queue named `codex`** is used for all tasks that call Codex CLI or Codex Cloud:

- **Requirement**: The automation platform MUST route all Codex phases through the `codex` queue so Codex-specific dependencies stay isolated from default workloads.:contentReference[oaicite:14]{index=14}
- Non-Codex tasks continue using the default queue; this prevents Codex jobs from starving general workloads and vice-versa.:contentReference[oaicite:15]{index=15}

Routing strategy (conceptual):

- In `celery_worker/speckit_worker.py`, Codex-related tasks (submit, poll/apply, PR) are declared with `queue="codex"` or are mapped via `task_routes`.
- Spec-agnostic tasks (e.g., status updates, simple bookkeeping) remain on Celery’s default queue.

---

## 3. Codex Worker Group

### 3.1 Definition

The **Codex worker group** is a set of Celery worker processes bound exclusively to the `codex` queue, using a shared container image and a **Codex auth volume**:

- **Codex Worker**: A Celery worker instance bound to the Codex queue and its corresponding auth volume; responsible for executing Codex phases of Spec runs.:contentReference[oaicite:16]{index=16}
- Initial rollout assumes a **single Codex worker** is sufficient, with the ability to add more workers on the same queue later.:contentReference[oaicite:17]{index=17}

Implementation entrypoints:

- `celery_worker/speckit_worker.py` – Celery worker bootstrap + Codex helpers.:contentReference[oaicite:18]{index=18}
- `docker-compose.yaml` – defines the `celery_codex_worker` service using the shared `x-celery-worker-base` image and a Codex-only queue (`SPEC_WORKFLOW_CODEX_QUEUE` defaulting to `codex`).:contentReference[oaicite:20]{index=20}

### 3.2 Scaling the Worker Group

Workers are deployed as a dedicated service (e.g., `celery_codex_worker`) with:

- A Celery app configured to listen only on the `codex` queue.
- The Codex auth volume mounted at the configured `CODEX_HOME` (e.g., `/home/app/.codex`).
- Codex CLI and Spec Kit CLI present on `PATH` via the shared automation image.:contentReference[oaicite:20]{index=20}

Scaling strategies:

- **Single instance (default)**: One Codex worker process listening on `codex` is sufficient for initial throughput.:contentReference[oaicite:21]{index=21}
- **Multiple instances (future)**: Additional Codex workers can be added to the same `codex` queue. Each worker instance MUST have its own dedicated auth volume (see §4) to avoid token clobbering when OAuth artifacts are updated.:contentReference[oaicite:22]{index=22}

Compose highlights:

- `celery_codex_worker` runs `celery -A celery_worker.speckit_worker worker --queues=${SPEC_WORKFLOW_CODEX_QUEUE:-codex}` to bind strictly to the Codex queue.
- The worker inherits the shared Celery image and mounts the Codex auth volume at `${CODEX_VOLUME_PATH:-/var/lib/codex-auth}` via `CODEX_VOLUME_NAME`.
- The managed Codex config template lives at `/app/api_service/config.template.toml` and is exposed through `CODEX_TEMPLATE_PATH` for non-interactive runs.

---

## 4. Codex Auth Volumes and Shared Credentials

### 4.1 Codex Auth Volume Concept

The specs define a **Codex Auth Volume** as:

> “Persistent storage that holds ChatGPT OAuth artifacts for a single Codex worker; uniquely named and reused across runs.”:contentReference[oaicite:23]{index=23}

Functional requirements:

- Each Codex worker MUST map exactly one named persistent Codex auth volume and mount it into every job container’s Codex configuration path.:contentReference[oaicite:24]{index=24}
- The platform MUST perform an automated login status check against that volume before any Codex phase executes, and block execution on failure.:contentReference[oaicite:25]{index=25}

### 4.2 Sharing the Volume Across Containers

Within a **single Codex worker group**, we share a **named Docker volume** across all containers that need Codex credentials:

- The Celery worker container mounts the volume at `CODEX_HOME` (e.g., `/home/app/.codex`) or `/var/lib/codex-auth` when using `CODEX_VOLUME_PATH` defaults from `docker-compose.yaml`.
- Any ephemeral **job container** launched by the worker for Spec runs also mounts **that same volume** at `CODEX_HOME` so Codex CLI sees the same OAuth state.:contentReference[oaicite:26]{index=26}

This achieves:

- **One-time login**: Operators authenticate the volume once; all Codex CLI invocations in that worker group reuse the same login without re-prompting.:contentReference[oaicite:27]{index=27}
- **Consistent CLI behavior**: Every container in the Codex worker group reads the same `.codex/config.toml` and token cache.

**Important constraints:**

- A Codex auth volume is intended for **one Codex worker**; accidentally sharing the same volume between multiple workers is treated as an edge case the platform should detect to prevent token clobbering.:contentReference[oaicite:28]{index=28}
- If a Codex worker starts without its configured auth volume, the run should fail fast with guidance instead of falling back to ephemeral, unauthenticated storage.:contentReference[oaicite:29]{index=29}

### 4.3 Authentication Flow (High Level)

A separate runbook will capture the exact commands; conceptually:

1. **Create the volume** (e.g., `codex_auth_worker1`) and attach it to a temporary shell container using the same image as the Codex worker.
2. **Run the Codex CLI login flow** inside that container so OAuth artifacts and `.codex/config.toml` are written into the volume.:contentReference[oaicite:30]{index=30}
3. **Verify login** via the pre-flight check (Codex CLI status or the worker’s own health probe) before enabling the worker to accept jobs.:contentReference[oaicite:31]{index=31}

Once the volume is authenticated, all future Codex runs executed by that worker group should complete without interactive re-authentication.:contentReference[oaicite:32]{index=32}

> **Headless setup requirement:** During setup, configure the Codex CLI’s managed `config.toml` with `approval_policy = "never"` so the workflow never pauses to request interactive approvals. This keeps Celery-driven runs fully non-interactive in headless environments.

---

## 5. Container Image and `.codex/config.toml`

The **automation runtime image** used by `api_service` and Celery workers is responsible for bundling Codex tooling:​:contentReference[oaicite:33]{index=33}

- **Codex CLI**: Installed at build time and available on the default `PATH` for the Celery service account.:contentReference[oaicite:34]{index=34}
- **GitHub Spec Kit CLI**: Installed alongside Codex so Spec Kit phases (discover, submit, publish) can run inside Celery tasks.:contentReference[oaicite:35]{index=35}

The build must also provision a **managed Codex configuration profile**:

- A `.codex/config.toml` under the worker user’s home directory containing `approval_policy = "never"`. In MoonMind’s Compose setup this is templated from `/app/api_service/config.template.toml` and written to the mounted auth volume path.:contentReference[oaicite:36]{index=36}:contentReference[oaicite:37]{index=37}
- This keeps Codex runs **non-interactive**, preventing Celery jobs from stalling on approvals.:contentReference[oaicite:38]{index=38}

Worker startup or health checks must verify:

- Codex CLI and Spec Kit CLI exist and are callable.
- The managed `.codex/config.toml` is present and readable.

Missing artifacts must block the worker from accepting jobs and emit actionable logs.:contentReference[oaicite:39]{index=39}

---

## 6. Interaction with the Celery Chain Workflow

Putting it all together:

1. **Workflow trigger**: MoonMind operator chooses “Run next Spec Kit phase” in the UI; the backend enqueues a Celery Chain representing the full workflow.:contentReference[oaicite:40]{index=40}
2. **Routing**:
   - Discovery and bookkeeping tasks can run on the default queue.:contentReference[oaicite:41]{index=41}
   - Codex submission, polling/apply, and PR tasks are routed to the `codex` queue.:contentReference[oaicite:42]{index=42}
3. **Execution on Codex worker**:
   - The Codex worker pulls tasks from the `codex` queue.
   - Before executing a Codex phase, it runs a pre-flight login check against its assigned auth volume.:contentReference[oaicite:43]{index=43}
   - Tasks invoke Codex CLI and Spec Kit CLI from the shared image, using OAuth state and config from the mounted volume.:contentReference[oaicite:44]{index=44}
4. **Status and artifacts**:
   - Each Celery task emits structured status updates (state, timestamps, payload references) that MoonMind surfaces in the UI.:contentReference[oaicite:45]{index=45}
   - Codex logs, patches, and PR URLs are stored as workflow artifacts linked to the `SpecWorkflowRun`.:contentReference[oaicite:46]{index=46}

If credentials expire or the auth volume is misconfigured, the chain **halts early** with a remediation message rather than leaving partial branches or silently failing.:contentReference[oaicite:47]{index=47}:contentReference[oaicite:48]{index=48}

---

## 7. Summary

- The **`codex` queue** isolates Codex-heavy workload from general Celery traffic and becomes the scaling knob for Codex capacity.:contentReference[oaicite:49]{index=49}
- The **Codex worker group** is one or more Celery workers bound to that queue, each with a dedicated **Codex auth volume**.:contentReference[oaicite:50]{index=50}
- A **named volume** stores shared OAuth artifacts and `.codex/config.toml`, mounted into every container in the worker group so CLI runs are non-interactive and persistent.:contentReference[oaicite:51]{index=51}:contentReference[oaicite:52]{index=52}
- The **automation image** packages Codex CLI and Spec Kit CLI, making Codex phases a first-class part of the Celery chain workflow that moves Spec Kit tasks from plan to PR.:contentReference[oaicite:53]{index=53}:contentReference[oaicite:54]{index=54}

This architecture allows MoonMind to scale Codex automation by adjusting Codex worker instances and their queues, without re-introducing manual authentication or ad-hoc CLI installs.
```
