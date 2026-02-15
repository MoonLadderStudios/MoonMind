# Gemini CLI Worker Architecture

This document describes how MoonMind uses a **Gemini-focused Celery worker group** to execute Spec Kit automation phases that depend on the Gemini CLI, how the **`gemini` queue** is used for routing, and how a **named persistent volume** is shared across containers to preserve authentication or configuration state.

---

## 1. Goals

The Gemini worker group exists to support the Celery chain that drives workflows relying on Google's Gemini models:

- Submit work to Gemini APIs via the CLI.
- Poll for results or handle streaming responses.
- Persist artifacts and emit structured status back to MoonMind’s UI.

The key goals, mirroring the Codex worker architecture:

- **Dedicated Gemini queue**: All Gemini-heavy tasks are isolated on a named `gemini` queue, separate from default Celery traffic.
- **Persistent Configuration/Auth**: Gemini CLI runs use a **shared volume** to persist authentication state (if using OAuth) or cached configuration, preventing repetitive setup.
- **Containerized tooling**: The worker image includes the `@google/gemini-cli` package installed from **public npm sources**, ensuring reproducible builds without private registry dependencies.

---

## 2. Celery Topology and the `gemini` Queue

### 2.1 Workflow Integration

Similar to the Codex workflow, Gemini tasks are part of a larger automation chain:

- `gemini_generate` – Invokes the Gemini CLI with a prompt or file input.
- `gemini_process_response` – Handles the output, parsing JSON or text.

### 2.2 `gemini` Queue

A dedicated **Celery queue named `gemini`** is used for all tasks that call the Gemini CLI:

- **Requirement**: The automation platform MUST route all Gemini phases through the `gemini` queue.
- This isolation prevents long-running or rate-limited Gemini tasks from blocking other system operations.

Routing strategy:
- Tasks are declared with `queue="gemini"` in the Celery configuration or task decorator.
- Spec-agnostic tasks remain on the default queue.

---

## 3. Gemini Worker Group

### 3.1 Definition

The **Gemini worker group** is a set of Celery worker processes bound exclusively to the `gemini` queue, using a shared container image and a **Gemini auth volume**:

- **Gemini Worker**: A Celery worker instance bound to the `gemini` queue and its corresponding auth volume.
- **Service Name**: `celery_gemini_worker` in `docker-compose.yaml`.

### 3.2 Scaling the Worker Group

Workers are deployed as a dedicated service with:

- A Celery app configured to listen only on the `gemini` queue.
- The Gemini auth volume mounted at the configured `GEMINI_HOME` (e.g., `/home/app/.gemini`).
- Gemini CLI present on `PATH` via the shared automation image.

Scaling strategies:
- **Single instance (default)**: One Gemini worker process is sufficient for initial workloads.
- **Multiple instances**: Additional workers can be added to the `gemini` queue. All instances share the same named volume to ensure consistent configuration and auth state.

---

## 4. Gemini Auth Volumes and Shared Credentials

### 4.1 Gemini Auth Volume Concept

The **Gemini Auth Volume** is persistent storage that holds:
- OAuth credentials (if using `gcloud` style auth).
- CLI configuration files.
- Cache data.

Functional requirements:
- The `celery_gemini_worker` service MUST mount a named volume (e.g., `gemini_auth_volume`).
- Even if the CLI primarily uses `GEMINI_API_KEY` from environment variables, this volume provides a forward-compatible slot for persistent state (like token caching or complex config profiles) similar to the Codex worker.

### 4.2 Sharing the Volume

Within a **single Gemini worker group**, we share a **named Docker volume**:

- The Celery worker container mounts the volume at `GEMINI_HOME`.
- This ensures that if the CLI requires a one-time "login" or setup command, it only needs to run once for the entire group.

---

## 5. Container Image and Dependencies

The **automation runtime image** bundles the Gemini tooling:

- **Gemini CLI**: Installed at build time via `npm install -g @google/gemini-cli`.
- **Public Sources**: The installation explicitly uses the **public npm registry**. This differs from early Codex setups that might have relied on private feeds. The Dockerfile uses build arguments (`INSTALL_GEMINI_CLI=true`) to trigger this installation.

Worker startup checks:
- Verify `gemini --version` returns successfully.
- Verify access to the `GEMINI_API_KEY` or auth volume credentials.

---

## 6. Summary

- The **`gemini` queue** isolates Gemini interactions.
- The **Gemini worker group** scales independently using the `celery_gemini_worker` service.
- A **named volume** (`gemini_auth_volume`) stores shared state, mirroring the robust Codex pattern.
- Dependencies are sourced from **public npm registries**, ensuring easy replication and updating.
