# OpenClaw Integration Design

Status: Draft  
Owners: MoonMind Eng  
Last Updated: 2026-02-19  
Related: `docker-compose.yaml`, `.env-template`, `services/openclaw/`, `tools/bootstrap-openclaw-codex-volume.sh`, `api_service/scripts/ensure_codex_config.py`

---

## Executive Summary

- The `openclaw` Compose profile makes the service opt-in so the default MoonMind stack stays unchanged until explicitly enabled.
- OpenClaw now mounts its **OWN CODEX AUTH VOLUME (NOT SHARED)** via `openclaw_codex_auth_volume`, eliminating credential collisions with workers that continue using `codex_auth_volume`.
- `tools/bootstrap-openclaw-codex-volume.sh` is the trigger script that clones or initializes the dedicated volume before `openclaw` starts, guaranteeing `codex login status` passes inside the container.
- Runtime code pins OpenClaw to `OPENCLAW_MODEL` and rejects/overrides per-request model overrides to keep configuration isolated from the rest of the platform.

## 1) Problem Statement

MoonMind must run **OpenClaw** as a first-class yet optional containerized service. The integration has three uncompromising requirements:

1. OpenClaw must sit behind a Compose profile named `openclaw` so the baseline stack is unchanged unless the profile is requested.
2. OpenClaw needs **its own Codex auth volume** that never shares disk state with the existing `codex_auth_volume`. Sharing credentials leads to token refresh collisions and cross-service logouts.
3. Operators need a **bootstrap script** that “triggers” the OpenClaw auth volume by cloning or initializing credentials so that `codex login status` passes before the service starts. This script is what actually prepares the dedicated Codex volume.

Additionally, OpenClaw must be pinned to exactly one LLM model, controlled centrally, without per-request overrides.

---

## 2) Goals / Non-Goals

**Goals**
- Add an `openclaw` profile + service to `docker-compose.yaml` on `local-network`.
- Reuse the Codex OAuth mechanism via a **separate** named volume (default `openclaw_codex_auth_volume`).
- Lock OpenClaw to the model defined in `OPENCLAW_MODEL`, enforced by both adapter code and an optional CLI shim.
- Provide `tools/bootstrap-openclaw-codex-volume.sh` to create/refresh/validate the dedicated auth volume.

**Non-Goals**
- Replacing MoonMind worker defaults or their Codex volume usage.
- Redesigning OpenClaw’s feature set beyond containerization, auth volume management, and model locking.
- Delivering a shared volume pathway—any such fallback is explicitly discouraged and documented only for emergencies.

---

## 3) Proposed Architecture

### 3.1 Service Topology
- Compose service name `openclaw` runs with `profiles: ["openclaw"]` so nothing happens unless that profile is specified (e.g., `docker compose --profile openclaw up openclaw`).
- The service joins the pre-existing external `local-network` to reach `api`, RabbitMQ, and Postgres.
- `depends_on.api.condition=service_started` delays OpenClaw until the API container is running but does not force two-way coupling.
- Restart policy: `unless-stopped` for parity with other MoonMind long-lived services.

### 3.2 Codex Auth Reuse Strategy (Dedicated Volume)
- Create a unique volume `openclaw_codex_auth_volume` named via `OPENCLAW_CODEX_VOLUME_NAME` and mount it at `${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}` inside the container.
- Only OpenClaw mounts this volume. The canonical `codex_auth_volume` remains attached to workers/orchestrator only.
- A second volume `openclaw_data` holds OpenClaw runtime caches or transcripts.
- Fallback (documented only as an emergency) is to mount `codex_auth_volume` directly, but the doc and bootstrap script default to the safer dedicated copy.

### 3.3 Model Lock Strategy
- Env var `OPENCLAW_MODEL` carries the single approved model (default `gpt-5.3-codex`).
- `OPENCLAW_MODEL_LOCK_MODE` controls how override attempts behave:
  - `force`: ignore requested models, log warning, still call the pinned model.
  - `reject`: raise/abort immediately before hitting Codex.
- Enforcement happens in both the Python adapter (`openclaw/llm.py`) and, if OpenClaw shells out to the Codex CLI, a shim script earlier in `$PATH`.

---

## 4) Configuration Surface (`.env-template`)

Add these OpenClaw-only entries near other service-specific settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENCLAW_ENABLED` | `false` | Helper flag for tooling to decide when to append the profile. |
| `OPENCLAW_MODEL` | `gpt-5.3-codex` | Sole model OpenClaw may use. |
| `OPENCLAW_MODEL_LOCK_MODE` | `force` | `force` or `reject` behavior. |
| `OPENCLAW_CODEX_VOLUME_NAME` | `openclaw_codex_auth_volume` | Docker volume used only by OpenClaw. |
| `OPENCLAW_CODEX_VOLUME_PATH` | `/home/app/.codex` | Mount path (mirrors Codex CLI expectations). |

We intentionally **do not** reuse `CODEX_MODEL` or `CODEX_VOLUME_NAME` to avoid mutating worker defaults. Other env vars (`MOONMIND_URL`, `MOONMIND_API_TOKEN`, `CODEX_ENV`) remain shared, so OpenClaw inherits connectivity without redefinition.

---

## 5) Docker Compose Changes

### 5.1 Volumes

```yaml
docker-compose.yaml

volumes:
  openclaw_data:
  openclaw_codex_auth_volume:
    name: ${OPENCLAW_CODEX_VOLUME_NAME:-openclaw_codex_auth_volume}
```

`openclaw_codex_auth_volume` is the dedicated Codex auth store. `openclaw_data` keeps runtime cache/state.

### 5.2 Service Definition

```yaml
services:
  openclaw:
    profiles: ["openclaw"]
    build:
      context: .
      dockerfile: ./services/openclaw/Dockerfile
    user: app
    env_file:
      - .env
    environment:
      - OPENCLAW_MODEL=${OPENCLAW_MODEL:-gpt-5.3-codex}
      - OPENCLAW_MODEL_LOCK_MODE=${OPENCLAW_MODEL_LOCK_MODE:-force}
      - CODEX_VOLUME_PATH=${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}
      - CODEX_HOME=${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}
      - CODEX_CONFIG_HOME=${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}
      - CODEX_CONFIG_PATH=${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}/config.toml
      - CODEX_ENV=${CODEX_ENV:-prod}
      - MOONMIND_URL=${MOONMIND_URL:-http://api:5000}
      - MOONMIND_API_TOKEN=${MOONMIND_API_TOKEN:-}
    volumes:
      - openclaw_codex_auth_volume:${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}
      - openclaw_data:/var/lib/openclaw
    networks:
      - local-network
    depends_on:
      api:
        condition: service_started
    restart: unless-stopped
```

### 5.3 Validation Commands

1. `docker compose --profile openclaw config` &rarr; ensures env interpolation, profile gating, and volumes resolve properly.
2. `docker compose --profile openclaw up openclaw` &rarr; runs the optional container without disturbing the rest of the stack.
3. `docker compose --profile openclaw down` &rarr; removes only OpenClaw resources, leaving other containers untouched.

---

## 6) OpenClaw Image + Entrypoint

- **Dockerfile** (`services/openclaw/Dockerfile`): base on the same Python 3.11 runtime as other services, install OpenClaw plus helper scripts, copy adapter code, and switch to the non-root `app` user.
- **Entrypoint** (`services/openclaw/entrypoint.sh`):
  1. `set -euo pipefail` and verify `OPENCLAW_MODEL` is populated.
  2. Run `python -m api_service.scripts.ensure_codex_config` to enforce Codex approval policy settings (mirrors other services).
  3. Validate credentials with `codex login status` scoped to `${OPENCLAW_CODEX_VOLUME_PATH}`.
  4. Emit logs naming the selected model + lock mode.
  5. Exec the OpenClaw server (e.g., `openclaw serve`).
- The container fails fast if any step above cannot complete, surfacing misconfiguration before the service handles traffic.

---

## 7) Model Lock Implementation Detail

### 7.1 Enforced Boundary
- `openclaw/llm.py` reads `OPENCLAW_MODEL` at import time and exposes a single `generate()` helper that never accepts `model` overrides.
- When upstream attempts to pass a different model, the adapter logs a warning (including the attempted model) and either overrides or raises according to `OPENCLAW_MODEL_LOCK_MODE`.
- Structured logs + StatsD metrics make it easy to audit override attempts.

### 7.2 Guardrail Wrapper
- If OpenClaw shells out to the Codex CLI, provide `services/openclaw/bin/codex` ahead of the real binary. The shim injects `--model "$OPENCLAW_MODEL"`, strips conflicting flags, and mirrors the adapter’s `force` vs `reject` behavior.
- Unit tests (`tests/openclaw/test_model_lock.py`) cover both code paths to satisfy FR-006/FR-008: forcing overrides never touches Codex with the wrong model, and reject mode raises before any network call.

---

## 8) Auth Bootstrapping Runbook (Triggering the Dedicated Volume)

### 8.1 Recommended Path: `tools/bootstrap-openclaw-codex-volume.sh`
1. Ensure Docker + `docker compose` are available and that `local-network` exists (create it if missing).
2. Verify the canonical Codex volume (`${CODEX_VOLUME_NAME:-codex_auth_volume}`) exists and is not mounted by a running container.
3. Refuse to continue if `openclaw_codex_auth_volume` is attached anywhere—this prevents overwriting live credentials.
4. Use an `alpine` tar pipe (or equivalent) to copy files from the shared volume into `openclaw_codex_auth_volume` atomically.
5. Run `docker compose --profile openclaw run --rm --user app openclaw bash -lc 'codex login status'` to validate that OpenClaw can authenticate using its own volume.
6. Print clear success/failure messages; exit non-zero on any error.

This script is what “triggers” the dedicated Codex auth volume. Run it during initial enablement and any time credentials rotate.

### 8.2 Script Flags & Idempotence
- `--from-scratch`: optional flag to skip copying and force an interactive login directly into the OpenClaw volume.
- `--verbose`: stream tar + validation output while keeping the default mode concise.
- Running the script multiple times is safe: it recreates the destination volume if empty and revalidates auth but never deletes existing credentials without confirmation.

### 8.3 Manual Alternative
If cloning is not allowed, operators can authenticate directly inside the optional container:

```bash
docker compose --profile openclaw run --rm --user app openclaw \
  bash -lc 'codex login --device-auth && codex login status'
```

This still uses the *dedicated* volume because the profile mounts `openclaw_codex_auth_volume` automatically.

---

## 9) Testing & Acceptance Criteria

1. `docker compose --profile openclaw up -d openclaw` creates `openclaw_codex_auth_volume` + `openclaw_data` and starts the service on `local-network` without touching other profiles.
2. `codex login status` succeeds inside the running OpenClaw container using only the dedicated volume.
3. Override attempts behave per `OPENCLAW_MODEL_LOCK_MODE` (log-and-force or reject) in both adapter and CLI shim tests.
4. Changing `OPENCLAW_MODEL` does **not** impact other workers (`MOONMIND_CODEX_MODEL`, `CODEX_MODEL` remain untouched).
5. `tools/bootstrap-openclaw-codex-volume.sh` exits non-zero for missing Docker tooling, missing source volume, destination-in-use, or failed credential checks.

---

## 10) Rollout Plan

1. Land `.env-template` updates introducing the OpenClaw env vars.
2. Add Compose volumes + service definition behind the `openclaw` profile.
3. Commit `services/openclaw/` Dockerfile, entrypoint, adapter, and CLI shim.
4. Add `tools/bootstrap-openclaw-codex-volume.sh` with executable bit and documentation.
5. Merge this doc and circulate the runbook.
6. Validate end-to-end by running `./tools/test_unit.sh` (adapter + script tests) before shipping.

---

## 11) Notes / Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Sharing Codex volume | Token refresh collisions, unpredictable logouts. | Default to dedicated volume + loud docs; script never touches shared volume unless explicitly configured. |
| Bootstrap while container is running | Could overwrite live credentials. | Script refuses to run if `openclaw_codex_auth_volume` is mounted. |
| Model override bypass | Policy compliance failure. | Two-layer enforcement (adapter + CLI shim) plus tests and log alerts. |
| Credential drift after rotation | OpenClaw fails to authenticate. | Re-run bootstrap script after rotations; it copies + validates every time. |

OpenClaw now slots cleanly into MoonMind’s Compose stack as an optional component with its **own Codex auth volume** and a dedicated bootstrap script that safely triggers that volume before the profile is ever enabled.
