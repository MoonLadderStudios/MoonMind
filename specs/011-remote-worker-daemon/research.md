# Research: Agent Queue Remote Worker Daemon (Milestone 3)

## Decision 1: Use REST queue APIs from worker runtime

- **Decision**: Implement a lightweight queue API client in the worker package using `httpx` against `/api/queue/jobs/*` and artifact endpoints.
- **Rationale**: The worker must run on remote machines outside API/Celery process boundaries, so direct repository/service imports are inappropriate.
- **Alternatives considered**:
  - Direct database access from worker: rejected due coupling, security risk, and bypassing API auth/validation.
  - MCP-only transport: rejected for milestone scope; REST is already implemented and sufficient.

## Decision 2: Worker preflight checks are hard startup gates

- **Decision**: On startup, verify `codex` executable via `verify_cli_is_executable("codex")`, then run `codex login status`; abort startup on failure.
- **Rationale**: Prevents workers from claiming jobs they cannot execute and matches the source-document fail-fast requirement.
- **Alternatives considered**:
  - Deferred checks after claim: rejected because it increases failed claims/noise and wastes lease windows.

## Decision 3: Heartbeat runs in a background async task per claimed job

- **Decision**: Start a heartbeat loop at interval `max(1s, lease_seconds / 3)` while a job executes; cancel loop on terminal state.
- **Rationale**: Satisfies documented cadence and supports long-running commands without blocking execution.
- **Alternatives considered**:
  - Inline heartbeat only between execution phases: rejected because single long phase (`codex exec`) can exceed lease.

## Decision 4: `codex_exec` handler produces deterministic local artifacts

- **Decision**: For each claimed `codex_exec` job, checkout repository, run `codex exec`, write stdout/stderr to log file, and capture `git diff` to patch artifact.
- **Rationale**: Covers mandated milestone behavior and keeps artifacts consistent for upload/inspection.
- **Alternatives considered**:
  - Stream command output directly without local files: rejected because artifact upload endpoint expects file payloads and deterministic file references simplify retries.

## Decision 5: Publish mode support is explicit and bounded

- **Decision**: Support `publish.mode` values `none`, `branch`, and `pr`; `none` is default, while `branch`/`pr` are best-effort subprocess steps and surfaced in execution summary.
- **Rationale**: Aligns with payload contract while keeping milestone scope focused on core execution path.
- **Alternatives considered**:
  - Ignore publish block entirely: rejected due documented payload shape.
  - Full workflow automation parity with orchestrator publish: rejected as out-of-scope for Milestone 3.

## Decision 6: Scope excludes `codex_skill` execution

- **Decision**: Milestone implementation handles `codex_exec`; unsupported job types are failed with explicit error summary.
- **Rationale**: Source milestone explicitly requires `codex_exec` end-to-end; `codex_skill` can be addressed later with dedicated planning.
- **Alternatives considered**:
  - Implement both job types now: rejected to preserve milestone focus and reduce risk.
