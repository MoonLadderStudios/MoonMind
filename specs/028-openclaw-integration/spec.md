# Feature Specification: OpenClaw Dedicated Integration

**Feature Branch**: `028-openclaw-integration`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: Enable OpenClaw as an optional docker-compose profile with its own Codex auth volume, pinned model, bootstrap script, and documentation while ensuring runtime code + validation tests ship alongside docs.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Launch OpenClaw Profile (Priority: P1)

As a platform engineer, I can start `docker compose --profile openclaw up openclaw` and the service joins `local-network`, reads `.env`, and mounts its own Codex auth + data volumes without affecting existing workers.

**Why this priority**: OpenClaw must run in parity with other MoonMind services before any downstream workflows or docs matter.

**Independent Test**: Boot a clean workspace, set required env vars, and run the compose command; service should reach healthy state without manual tweaks to other components.

**Acceptance Scenarios**:

1. **Given** `.env` includes OpenClaw env vars, **When** the engineer uses the `openclaw` profile, **Then** compose creates `openclaw_codex_auth_volume` and `openclaw_data` and the container starts on `local-network`.
2. **Given** OpenClaw is optional, **When** the engineer runs compose without the profile, **Then** no OpenClaw containers or volumes are created and other services remain unaffected.

---

### User Story 2 - Bootstrap Dedicated Codex Auth (Priority: P2)

As an infra operator, I can clone credentials from the shared `codex_auth_volume` into the OpenClaw volume (or authenticate directly) using a supported script so OpenClaw passes `codex login status` before it processes traffic.

**Why this priority**: Without isolated credentials, OpenClaw would clobber worker tokens, breaking the primary MoonMind workflow.

**Independent Test**: Run `tools/bootstrap-openclaw-codex-volume.sh` on a workstation and verify `codex login status` inside an OpenClaw container succeeds without touching other volumes.

**Acceptance Scenarios**:

1. **Given** the shared Codex volume already contains valid auth, **When** the operator runs the bootstrap script, **Then** the OpenClaw volume is created (if needed) and receives an exact copy plus a validation check.
2. **Given** no shared volume copy is desired, **When** the operator runs `docker compose --profile openclaw run --rm openclaw codex login --device-auth`, **Then** the resulting auth artifacts stay isolated within `openclaw_codex_auth_volume`.

---

### User Story 3 - Enforce a Single OpenClaw Model (Priority: P3)

As a security engineer, I can see that OpenClaw hard-locks to `OPENCLAW_MODEL` with a configurable lock mode so downstream requests cannot silently switch models at runtime.

**Why this priority**: The organization must guarantee OpenClaw never bypasses approved LLM policies even if plugins or UI attempts to override them.

**Independent Test**: Run automated unit tests covering the adapter/wrapper to simulate override attempts under `force` and `reject` modes and confirm logs or exceptions align with policy.

**Acceptance Scenarios**:

1. **Given** `OPENCLAW_MODEL_LOCK_MODE=force`, **When** OpenClaw receives a request that specifies a different model, **Then** it logs an override warning and still calls the pinned model.
2. **Given** `OPENCLAW_MODEL_LOCK_MODE=reject`, **When** a different model is requested, **Then** the request fails fast, logs the denial, and no Codex call happens.

---

### Edge Cases

- Compose profile invoked with missing `OPENCLAW_MODEL`: service startup must fail with a clear log before touching Codex.
- Bootstrap script run twice: script should remain idempotent and avoid deleting existing auth.
- Shared Codex volume offline or missing: script must exit with actionable guidance instead of creating a partially populated OpenClaw volume.
- Model override attempt occurs before OpenClaw loads env vars: adapter must default to a safe failure rather than calling Codex with `None`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Compose defines an `openclaw` service under `profiles: ["openclaw"]` that depends on `api`, joins `local-network`, and mounts two volumes: `openclaw_codex_auth_volume` at `${OPENCLAW_CODEX_VOLUME_PATH:-/home/app/.codex}` and `openclaw_data` at `/var/lib/openclaw`.
- **FR-002**: `.env-template` adds OpenClaw-specific variables (`OPENCLAW_ENABLED`, `OPENCLAW_MODEL`, `OPENCLAW_MODEL_LOCK_MODE`, `OPENCLAW_CODEX_VOLUME_NAME`, `OPENCLAW_CODEX_VOLUME_PATH`) with safe defaults that do not mutate existing worker values.
- **FR-003**: Compose declares named volumes `openclaw_codex_auth_volume` (using `${OPENCLAW_CODEX_VOLUME_NAME}`) and `openclaw_data`, ensuring they are optional unless the profile is used.
- **FR-004**: OpenClaw’s container image (Dockerfile + entrypoint) packages `api_service/scripts/ensure_codex_config.py`, validates `OPENCLAW_MODEL`, runs the script, surfs `codex login status`, and only then launches the OpenClaw server under the `app` user.
- **FR-005**: Provide a bootstrap helper `tools/bootstrap-openclaw-codex-volume.sh` that copies credentials from `codex_auth_volume` (or bail with guidance) and verifies them, supporting both local Docker and WSL flows.
- **FR-006**: Implement an OpenClaw LLM adapter or CLI wrapper that enforces `OPENCLAW_MODEL` using the `force`/`reject` modes, emits audit logs, and exposes automated unit tests via `./tools/test_unit.sh`.
- **FR-007**: Document the OpenClaw integration, bootstrap runbook, model lock behavior, and smoke tests in `docs/OpenClawIntegration.md`, ensuring instructions reference actual commands and env vars.
- **FR-008**: Add validation tests (unit or integration) that cover the bootstrap script error handling and the model lock adapter, executed via `./tools/test_unit.sh` in CI.

### Key Entities *(include if feature involves data)*

- **OpenClawServiceConfig**: Logical representation of compose settings (profile flag, dependency graph, networks, restart policy, env var wiring) consumed by operators.
- **OpenClawCodexVolume**: Dedicated credential store identified by `OPENCLAW_CODEX_VOLUME_NAME` and `OPENCLAW_CODEX_VOLUME_PATH`; holds auth tokens and config files that must never be shared across services.
- **ModelLockPolicy**: Enum-like configuration (`force`, `reject`) controlling how override attempts are handled, persisted via env vars and enforced by the adapter.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `docker compose --profile openclaw up -d openclaw` completes in under 2 minutes on a clean workstation and the container reports healthy status without modifying non-profile services.
- **SC-002**: `codex login status` inside the running OpenClaw container succeeds 100% of the time when the bootstrap script or manual login has been executed.
- **SC-003**: Automated adapter tests demonstrate that 100% of override attempts either log-and-force or reject exactly as configured, preventing any Codex calls with unauthorized models.
- **SC-004**: Bootstrap script exits with a non-zero status and actionable message for every simulated failure (missing source volume, network issue, permission error), ensuring operators never end up with silent partial copies.

## Assumptions & Dependencies

- OpenClaw source code (or binary) can run from `services/openclaw` alongside existing MoonMind tooling and depends on the same Python 3.11 runtime.
- Codex CLI and `api_service` scripts already exist in the repo, so OpenClaw can reuse them without duplicating logic.
- Operators have Docker access to run bootstrap/compose commands and can provide interactive auth if copying credentials is not allowed.
- Existing CI harness (`./tools/test_unit.sh`) is capable of invoking new adapter tests without additional orchestrator changes.

## Risks & Mitigations

- **Risk**: Shared credential misuse if operators accidentally mount `codex_auth_volume`. **Mitigation**: Document explicit warning plus compose comments and validation in bootstrap script.
- **Risk**: Model lock bypass via direct `codex` binary usage. **Mitigation**: Provide wrapper/adapter at the only invocation site and include tests.
- **Risk**: Volume bootstrap failure leaves stale data. **Mitigation**: Script uses atomic tar copy and verifies `codex login status` before exiting.
- **Risk**: Optional service causes confusion in environments without OpenClaw. **Mitigation**: Behind profile and disabled defaults, plus doc describing prerequisites.
