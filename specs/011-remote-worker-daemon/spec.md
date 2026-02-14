# Feature Specification: Agent Queue Remote Worker Daemon (Milestone 3)

**Feature Branch**: `011-remote-worker-daemon`  
**Created**: 2026-02-13  
**Status**: Draft  
**Input**: User description: "Implement Milestone 3 of docs/CodexTaskQueue.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Worker Executes `codex_exec` Jobs End-to-End (Priority: P1)

As a remote executor, I can claim queued `codex_exec` jobs and run Codex instructions so work created on one machine can execute on another.

**Why this priority**: Milestone 3 is fundamentally about delivering a functioning remote worker daemon for cross-machine execution.

**Independent Test**: Start the worker daemon, provide a queued `codex_exec` job, and verify the worker claims the job, runs Codex, and drives a terminal queue status.

**Acceptance Scenarios**:

1. **Given** a queued `codex_exec` job and an authenticated worker, **When** the daemon loop runs, **Then** the job is claimed, executed, and transitioned to `succeeded` or `failed`.
2. **Given** no eligible jobs, **When** the daemon loop runs, **Then** the worker continues polling without crashing.

---

### User Story 2 - Worker Publishes Execution Artifacts (Priority: P1)

As a queue operator, I can review worker outputs in MoonMind so remote execution remains auditable and actionable.

**Why this priority**: Without artifact publication, remote execution outcomes are opaque and cannot be consumed by producers.

**Independent Test**: Execute one `codex_exec` job and confirm logs/patch artifacts are uploaded through Milestone 2 artifact APIs.

**Acceptance Scenarios**:

1. **Given** a successful execution, **When** the handler finishes, **Then** it uploads execution log and patch artifacts tied to the job.
2. **Given** a failing execution, **When** the handler reports failure, **Then** failure details and available artifacts are still uploaded before terminal status.

---

### User Story 3 - Worker Lease Heartbeat and Crash Recovery (Priority: P2)

As a platform owner, I need lease renewal and reclaim behavior so stuck workers do not permanently block jobs.

**Why this priority**: Reliability for long-running jobs depends on heartbeat renewal and proper recovery when workers stop unexpectedly.

**Independent Test**: Validate heartbeat cadence while running and verify jobs become reclaimable after worker interruption and lease expiry.

**Acceptance Scenarios**:

1. **Given** a running claimed job, **When** processing exceeds one heartbeat interval, **Then** the worker sends heartbeat updates at approximately `leaseSeconds/3`.
2. **Given** a worker crash during execution, **When** lease expiration passes, **Then** jobs become reclaimable by another worker via existing queue logic.

### Edge Cases

- Worker startup when `codex` is missing from PATH.
- Worker startup when `codex login status` reports unauthenticated state.
- Job payload missing required `codex_exec` fields (`repository`, `instruction`) or containing unsupported values.
- Repository checkout or `codex exec` failure after claim.
- Artifact upload failure after execution output is generated.
- Unsupported job types claimed by worker allowlist configuration.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/CodexTaskQueue.md:470`, `docs/CodexTaskQueue.md:472`): Milestone 3 MUST add a remote worker daemon with `moonmind-codex-worker` CLI.
- **DOC-REQ-002** (Source: `docs/CodexTaskQueue.md:314`, `docs/CodexTaskQueue.md:320`): Worker packaging MUST include `moonmind/agents/codex_worker/{worker,handlers,cli}.py` and poetry script entrypoint wiring.
- **DOC-REQ-003** (Source: `docs/CodexTaskQueue.md:304`, `docs/CodexTaskQueue.md:308`): Worker daemon loop MUST claim jobs and maintain lease heartbeats.
- **DOC-REQ-004** (Source: `docs/CodexTaskQueue.md:305`, `docs/CodexTaskQueue.md:477`): Worker MUST execute `codex_exec` jobs via Codex CLI.
- **DOC-REQ-005** (Source: `docs/CodexTaskQueue.md:306`, `docs/CodexTaskQueue.md:479`): Worker MUST upload execution artifacts back to MoonMind.
- **DOC-REQ-006** (Source: `docs/CodexTaskQueue.md:307`, `docs/CodexTaskQueue.md:480`): Worker MUST mark jobs complete on success and failed on error.
- **DOC-REQ-007** (Source: `docs/CodexTaskQueue.md:327`, `docs/CodexTaskQueue.md:334`): Worker runtime MUST support documented environment variables and defaults for polling, lease, identity, and workdir.
- **DOC-REQ-008** (Source: `docs/CodexTaskQueue.md:338`, `docs/CodexTaskQueue.md:339`): Worker startup MUST verify Codex CLI availability and authenticated login state.
- **DOC-REQ-009** (Source: `docs/CodexTaskQueue.md:351`, `docs/CodexTaskQueue.md:356`): `codex_exec` payload handling MUST support repository/ref/workdir mode/instruction/publish inputs.
- **DOC-REQ-010** (Source: `docs/CodexTaskQueue.md:361`, `docs/CodexTaskQueue.md:365`): `codex_exec` flow MUST include checkout, codex execution, log capture, patch generation, and optional publish hook behavior.
- **DOC-REQ-011** (Source: `docs/CodexTaskQueue.md:389`, `docs/CodexTaskQueue.md:392`, `docs/CodexTaskQueue.md:481`): Worker MUST renew leases periodically and preserve crash recovery semantics via lease expiry/reclaim.
- **DOC-REQ-012** (Source: `docs/CodexTaskQueue.md:321`): Worker daemon MUST run independently from Celery worker bootstrap.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-002`, `DOC-REQ-012`): The system MUST provide a standalone `moonmind-codex-worker` CLI that starts a non-Celery daemon loop and is packaged through the repository's standard entrypoint mechanism.
- **FR-002** (`DOC-REQ-003`, `DOC-REQ-007`): The worker MUST poll queue claim endpoints with configurable worker identity, poll interval, and lease duration from environment settings.
- **FR-003** (`DOC-REQ-004`, `DOC-REQ-009`, `DOC-REQ-010`): The worker MUST execute `codex_exec` jobs by preparing a local checkout, running `codex exec` with the provided instruction, and collecting execution outputs.
- **FR-004** (`DOC-REQ-005`, `DOC-REQ-006`): The worker MUST upload generated log/patch artifacts and then issue job completion/failure transitions with summary/error details.
- **FR-005** (`DOC-REQ-008`): Worker startup MUST fail fast when Codex CLI is unavailable or not authenticated.
- **FR-006** (`DOC-REQ-011`): The worker MUST issue lease heartbeat renewals during execution and tolerate crash scenarios such that lease expiry allows job reclaim.
- **FR-007**: Runtime deliverables MUST include production runtime code and validation tests; docs-only changes are insufficient.

### Key Entities *(include if feature involves data)*

- **CodexWorkerConfig**: Environment-derived runtime configuration for queue URL, worker identity, polling cadence, lease duration, auth token, and local workdir.
- **CodexExecJobPayload**: Structured queue job payload for repository/ref/instruction/publish settings consumed by the worker handler.
- **WorkerExecutionResult**: Normalized handler outcome containing status, summary/error detail, and produced artifact file references.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `moonmind-codex-worker` CLI starts successfully when prerequisites are met and exits with clear error when Codex prerequisites fail.
- **SC-002**: A queued `codex_exec` job can be processed end-to-end by the daemon, including claim, execution, artifact upload, and terminal status update.
- **SC-003**: Automated unit tests verify startup validation, daemon poll/claim flow, and `codex_exec` handler happy/error paths.
- **SC-004**: Automated tests validate heartbeat cadence behavior and crash-recovery compatibility (reclaimable post-expiry semantics).
- **SC-005**: Milestone 3 unit tests pass through `./tools/test_unit.sh`.

## Assumptions

- Milestone 3 scope focuses on `codex_exec` end-to-end execution; `codex_skill` remains out-of-scope for implementation depth unless needed for shared worker abstractions.
- Artifact upload/list/download APIs from Milestone 2 are available for worker artifact publication.
- Worker token/OIDC hardening from Milestone 5 is out-of-scope for this milestone.
