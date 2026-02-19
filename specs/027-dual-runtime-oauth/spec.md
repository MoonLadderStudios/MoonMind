# Feature Specification: Dual OAuth Setup for Codex + Claude with Default Task Runtime

**Feature Branch**: `027-dual-runtime-oauth`  
**Created**: 2026-02-19  
**Status**: Draft  
**Input**: User description: "Add dual OAuth setup for Codex and Claude with default task runtime fallback. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operators authenticate both CLIs in one environment (Priority: P1)

As a MoonMind operator, I can complete Codex OAuth and Claude OAuth independently in the same deployment so workers can execute both runtime types without reconfiguring auth between runs.

**Why this priority**: Without persistent auth for both CLIs, mixed-runtime task execution is unreliable and requires manual reconfiguration.

**Independent Test**: Run both auth scripts once, restart worker containers, and verify runtime checks still detect valid auth state for both CLIs.

**Acceptance Scenarios**:

1. **Given** a fresh local deployment, **When** the operator runs `./tools/auth-codex-volume.sh` and `./tools/auth-claude-volume.sh`, **Then** both CLI auth states are persisted and available after container restart.
2. **Given** Codex and Claude auth are both configured, **When** a worker starts in a runtime mode that requires either or both CLIs, **Then** startup validation reports each required auth state without interactive prompts.

---

### User Story 2 - Runtime preflight enforces required auth by worker mode (Priority: P1)

As a platform engineer, I need preflight validation to check only the runtime auth needed for the configured worker mode (`codex`, `claude`, `universal`) so failures are explicit and actionable.

**Why this priority**: Runtime-specific preflight is the reliability gate that prevents workers from entering a broken claim loop.

**Independent Test**: Start preflight in each runtime mode with controlled credential availability and verify pass/fail behavior and error messaging.

**Acceptance Scenarios**:

1. **Given** `MOONMIND_WORKER_RUNTIME=codex`, **When** preflight runs, **Then** it validates Codex auth status and does not fail because Claude auth is absent.
2. **Given** `MOONMIND_WORKER_RUNTIME=claude`, **When** preflight runs, **Then** it validates Claude auth status and does not require Codex login.
3. **Given** `MOONMIND_WORKER_RUNTIME=universal`, **When** preflight runs, **Then** it validates both Codex and Claude auth and fails if either required auth state is missing.

---

### User Story 3 - Tasks without runtime use a configurable default (Priority: P2)

As a queue operator, I can set a deployment-level default task runtime while keeping explicit task runtime overrides authoritative.

**Why this priority**: Operators need predictable runtime routing for tasks created without explicit `targetRuntime`.

**Independent Test**: Enqueue one task without runtime and one with explicit runtime override, then verify normalized payload runtime values and required capabilities.

**Acceptance Scenarios**:

1. **Given** `MOONMIND_DEFAULT_TASK_RUNTIME=claude`, **When** a task is created without `targetRuntime`/`task.runtime.mode`, **Then** the normalized payload uses `claude`.
2. **Given** `MOONMIND_DEFAULT_TASK_RUNTIME=claude`, **When** a task payload explicitly sets `targetRuntime=codex`, **Then** the explicit value is preserved.

---

### Edge Cases

- `MOONMIND_DEFAULT_TASK_RUNTIME` is invalid or unsupported; settings validation should fail fast with a clear message.
- Worker runtime is `universal` but worker token capabilities exclude one runtime; claim filtering should continue to enforce capability policy.
- Codex auth is valid but Claude auth is missing; `codex` worker mode should still start while `claude`/`universal` should fail with actionable guidance.
- Existing codex-only deployments with no Claude settings should remain operational without new required environment variables.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support persistent Claude OAuth storage using dedicated configuration variables for volume name/path and home path, parallel to the existing Codex auth volume behavior.
- **FR-002**: The repository MUST provide `tools/auth-claude-volume.sh` to perform Claude OAuth login and immediate auth-status verification using the worker runtime container.
- **FR-003**: The existing `tools/auth-codex-volume.sh` workflow MUST remain supported and unchanged for Codex-only operators.
- **FR-004**: Worker preflight MUST validate runtime auth requirements by mode: `codex` checks Codex auth, `claude` checks Claude auth, and `universal` checks both.
- **FR-005**: The system MUST expose a configurable default task runtime (for example `MOONMIND_DEFAULT_TASK_RUNTIME`) applied only when task runtime is omitted.
- **FR-006**: Explicit runtime values in task payloads (`targetRuntime` or `task.runtime.mode`) MUST take precedence over the configured default task runtime.
- **FR-007**: Queue payload normalization and capability derivation for defaulted tasks MUST remain consistent with the canonical task contract.
- **FR-008**: `docker-compose.yaml` and `.env-template` MUST document and wire dual-auth configuration so operators can run two simple auth commands without ad-hoc configuration edits.
- **FR-009**: Existing codex-only behavior MUST remain backward compatible when Claude auth settings are not configured.

### Key Entities *(include if feature involves data)*

- **Codex Auth Volume Config**: Existing persisted CLI auth location used by Codex login checks and worker startup.
- **Claude Auth Volume Config**: New persisted CLI auth location used by Claude login checks and worker startup.
- **Worker Runtime Auth Profile**: Runtime-mode-specific preflight requirement set (`codex`, `claude`, `universal`).
- **Default Task Runtime Setting**: Deployment-level value used as fallback runtime for canonical task normalization when no explicit runtime is present.

## Assumptions

- Claude CLI supports a deterministic non-interactive auth-status command suitable for preflight validation.
- Queue worker token policy remains separate from CLI OAuth state and continues to gate claim authorization.
- Existing task runtime values continue to use supported execution runtimes (`codex`, `gemini`, `claude`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of local validation runs can complete both auth setup commands (`auth-codex-volume.sh`, `auth-claude-volume.sh`) without manual file edits to runtime container homes.
- **SC-002**: Preflight tests for `codex`, `claude`, and `universal` modes match expected runtime-specific auth pass/fail outcomes in unit coverage.
- **SC-003**: Task creation tests demonstrate that omitted runtime fields default to configured `MOONMIND_DEFAULT_TASK_RUNTIME` while explicit runtime overrides remain unchanged.
- **SC-004**: Existing codex-only unit tests continue to pass without requiring Claude-specific environment configuration.
