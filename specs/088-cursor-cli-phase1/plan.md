# Implementation Plan: Cursor CLI Phase 1 — Binary Integration

**Branch**: `088-cursor-cli-phase1` | **Date**: 2026-03-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/088-cursor-cli-phase1/spec.md`
**Source Contract**: `docs/ManagedAgents/CursorCli.md` (Sections 2, 3, 9, 13-Phase1)

## Summary

Install the Cursor CLI binary (`agent`) into the MoonMind worker Docker image, create an auth provisioning script following the existing `tools/auth-*-volume.sh` pattern, add Docker Compose volume and init service definitions, and document `CURSOR_API_KEY` in the environment template. This is Phase 1 (binary integration) — no adapter/runtime code changes are in scope.

## Technical Context

**Language/Version**: Bash (shell scripts), Dockerfile (Docker), YAML (Docker Compose)
**Primary Dependencies**: Docker, Docker Compose, Cursor CLI binary (`agent` from cursor.com/install)
**Storage**: Docker named volume `cursor_auth_volume` for persistent auth state
**Testing**: Manual verification via `agent --version`, `agent status`; shell script mode tests
**Target Platform**: Linux (Docker container, amd64)
**Project Type**: Infrastructure/DevOps — modifying existing Docker and shell tooling
**Constraints**: No adapter code changes (Phase 2), no database migrations (Phase 3)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | PASS | Adding a new runtime adapter slot — Cursor CLI runs as-is |
| II. One-Click Deployment | PASS | Cursor install is a single curl command; volume/init follow existing patterns |
| III. Avoid Vendor Lock-In | PASS | Cursor CLI is behind an adapter interface; adding it requires no core changes |
| V. Skills Are First-Class | N/A | Phase 1 is binary-only; skill wiring deferred to Phase 4 |
| VII. Powerful Runtime Configurability | PASS | `CURSOR_API_KEY` documented in env template with defaults |
| VIII. Modular and Extensible | PASS | New auth script + compose definitions — no core code changes |
| IX. Resilient by Default | PASS | Auth script has verification mode; build fails fast if install fails |

## Project Structure

### Documentation (this feature)

```text
specs/088-cursor-cli-phase1/
├── plan.md                           # This file
├── research.md                       # Phase 0 output
├── contracts/
│   └── requirements-traceability.md  # DOC-REQ mapping
└── tasks.md                          # Phase 2 output (speckit-tasks)
```

### Source Code (repository root)

```text
api_service/
└── Dockerfile              # MODIFY: add Cursor CLI install step

tools/
├── auth-gemini-volume.sh   # EXISTING: reference pattern
├── auth-claude-volume.sh   # EXISTING: reference pattern
├── auth-codex-volume.sh    # EXISTING: reference pattern
└── auth-cursor-volume.sh   # NEW: Cursor CLI auth provisioning script

docker-compose.yaml         # MODIFY: add cursor_auth_volume, cursor-auth-init, worker mount

.env-template               # MODIFY: add CURSOR_API_KEY documentation
```

**Structure Decision**: No new directories needed. Files are added to existing `tools/` and modify existing Docker infrastructure files.

## Implementation Details

### T1: Install Cursor CLI in Worker Dockerfile (DOC-REQ-001)

Add to `api_service/Dockerfile` after existing CLI tool installs:

```dockerfile
# Install Cursor CLI
RUN curl https://cursor.com/install -fsS | bash \
    && mv /root/.local/bin/agent /usr/local/bin/cursor-agent \
    && chmod +x /usr/local/bin/cursor-agent
```

> **Binary naming**: Use `cursor-agent` instead of `agent` to avoid namespace conflicts with any existing `agent` binary or shell alias on the PATH. The `ManagedRuntimeLauncher` will invoke `cursor-agent` directly.

### T2: Create `tools/auth-cursor-volume.sh` (DOC-REQ-002)

Follow the `auth-gemini-volume.sh` pattern with three modes:

| Mode | Behavior |
|------|----------|
| `--api-key` | Store `CURSOR_API_KEY` in the auth volume for container access |
| `--login` | Run `cursor-agent login` interactively inside a container |
| `--check` | Run `cursor-agent status` to verify auth state |
| `--register` | Register the profile via MoonMind API (runtime_id=cursor_cli) |

Environment overrides: `CURSOR_VOLUME_NAME`, `CURSOR_VOLUME_PATH`, `CURSOR_API_KEY`.

### T3: Docker Compose Changes (DOC-REQ-007)

Add to `docker-compose.yaml`:

**Volumes section** (~line 723):
```yaml
cursor_auth_volume:
  name: ${CURSOR_VOLUME_NAME:-cursor_auth_volume}
```

**Init service**:
```yaml
cursor-auth-init:
  image: alpine:3.20
  volumes:
    - cursor_auth_volume:/home/app/.cursor
  entrypoint: >
    sh -c "
      mkdir -p /home/app/.cursor &&
      chown -R 1000:1000 /home/app/.cursor &&
      chmod 0775 /home/app/.cursor
    "
  restart: "no"
  profiles:
    - init
```

**Worker volume mount** (sandbox + agent-runtime workers):
```yaml
- cursor_auth_volume:/home/app/.cursor
```

### T4: Document `CURSOR_API_KEY` in `.env-template` (DOC-REQ-004)

Add near existing API key entries:

```bash
# Cursor CLI API key for managed agent runtime (optional)
# Required for Cursor CLI headless execution when using API key auth mode.
# Obtain from cursor.com account settings.
# CURSOR_API_KEY=
```

### T5: Verify Container Functionality (DOC-REQ-003, DOC-REQ-005)

Post-build verification steps:
1. `docker build` succeeds with Cursor CLI installed
2. `docker run --rm <image> cursor-agent --version` returns valid version
3. `docker run --rm -e CURSOR_API_KEY=<key> <image> cursor-agent status` shows authenticated
4. Auto-update: document that the Docker image pins the version at build time (no runtime auto-update)

## Complexity Tracking

No constitution violations. All changes follow existing patterns.
