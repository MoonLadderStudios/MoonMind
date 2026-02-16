# Feature Specification: Unified CLI Single Queue Worker Runtime

**Feature Branch**: `018-unified-cli-queue`  
**Created**: 2026-02-16  
**Status**: Draft  
**Input**: User description: "Implement docs/UnifiedCliSingleQueueArchitecture.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/UnifiedCliSingleQueueArchitecture.md` §1 Summary | The platform must use one shared worker image that includes `codex`, `gemini`, `claude`, and `speckit`. |
| DOC-REQ-002 | `docs/UnifiedCliSingleQueueArchitecture.md` §1 Summary, §5.1 | `speckit` must remain bundled in the same worker Dockerfile and not be separated into another image pipeline. |
| DOC-REQ-003 | `docs/UnifiedCliSingleQueueArchitecture.md` §1 Summary, §5.2 | Worker execution jobs must use a single queue named `moonmind.jobs`. |
| DOC-REQ-004 | `docs/UnifiedCliSingleQueueArchitecture.md` §1 Summary, §5.3 | Worker runtime mode must be selected by `MOONMIND_WORKER_RUNTIME` with allowed values `codex`, `gemini`, `claude`, and `universal`. |
| DOC-REQ-005 | `docs/UnifiedCliSingleQueueArchitecture.md` §1 Summary, §5.4 | Job payloads must be runtime-neutral by default so any runtime worker can execute the same payload. |
| DOC-REQ-006 | `docs/UnifiedCliSingleQueueArchitecture.md` §5.4 | Optional targeted runtime execution must be supported via payload metadata and handled by universal mode workers. |
| DOC-REQ-007 | `docs/UnifiedCliSingleQueueArchitecture.md` §6.4 | Compose topology must support both homogeneous and mixed runtime fleets while still consuming one queue. |
| DOC-REQ-008 | `docs/UnifiedCliSingleQueueArchitecture.md` §6.5 | Runtime credentials must be injected at runtime (mounts/secrets/env), never baked into container images. |
| DOC-REQ-009 | `docs/UnifiedCliSingleQueueArchitecture.md` §8 | Worker startup must validate required CLI binaries and block job consumption when checks fail. |
| DOC-REQ-010 | `docs/UnifiedCliSingleQueueArchitecture.md` §7 | Migration must include phased queue consolidation and compatibility handling before removing legacy queue vars. |
| DOC-REQ-011 | `docs/UnifiedCliSingleQueueArchitecture.md` §8 | Observability must include runtime-tagged job counters/latency and queue/execution timing metrics. |
| DOC-REQ-012 | `docs/UnifiedCliSingleQueueArchitecture.md` §10 | Final implementation must satisfy single-image, single-queue, env-driven runtime mode, and no-thrash execution acceptance criteria. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate a Single Queue Runtime Fleet (Priority: P1)

As a platform engineer, I can run workers in different runtime modes from one shared image and one queue, so I can scale capacity without maintaining runtime-specific queue routing.

**Why this priority**: This is the core production behavior and directly delivers the architecture outcome requested by the source document.

**Independent Test**: Start workers in multiple runtime modes against `moonmind.jobs`, enqueue runtime-neutral jobs, and verify jobs are consumed and completed without requeue thrash.

**Acceptance Scenarios**:

1. **Given** the worker image includes all required CLIs, **When** workers start with `MOONMIND_WORKER_RUNTIME=codex|gemini|claude`, **Then** each worker consumes from `moonmind.jobs` and executes runtime-neutral jobs.
2. **Given** workers are using one shared queue, **When** a runtime-neutral job is claimed, **Then** the job completes on the claiming worker without runtime mismatch requeue loops.

---

### User Story 2 - Deploy Homogeneous or Mixed Runtime Services (Priority: P2)

As an operator, I can deploy either homogeneous or mixed runtime worker services through compose/env configuration, so I can choose operational topology without changing queue architecture.

**Why this priority**: Deployment flexibility is required for staged rollout and production scaling patterns.

**Independent Test**: Configure one deployment with a single runtime and one deployment with mixed runtimes, then validate both use `moonmind.jobs` and process jobs successfully.

**Acceptance Scenarios**:

1. **Given** a homogeneous runtime deployment, **When** services are started, **Then** all workers subscribe to `moonmind.jobs` and execute jobs.
2. **Given** a mixed runtime deployment, **When** services are started together, **Then** all workers subscribe to `moonmind.jobs` with runtime behavior determined only by env mode.

---

### User Story 3 - Enforce Safe Runtime and Tooling Contracts (Priority: P3)

As a maintainer, I can rely on startup health checks, runtime-neutral payload contracts, and traceable migration steps, so rollout remains observable and safe.

**Why this priority**: Safety and operability protect production workflows during queue/routing consolidation.

**Independent Test**: Validate startup fails when a required CLI is unavailable, ensure metrics include runtime labels, and verify migration compatibility behavior.

**Acceptance Scenarios**:

1. **Given** a required CLI binary is unavailable, **When** the worker starts, **Then** it refuses to accept jobs and emits actionable logs.
2. **Given** migration compatibility mode is enabled, **When** legacy queue inputs are present, **Then** compatibility handling processes them until deprecation is completed.

### Edge Cases

- What happens when `MOONMIND_WORKER_RUNTIME` has an invalid value at startup?
- How does execution behave when a runtime-targeted job arrives but no universal worker is running?
- How does the system behave when one CLI is present as a stub/fallback and must be treated as unhealthy?
- What happens when legacy queue variables are still set during single-queue cutover?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The worker image build pipeline MUST produce one image that includes executable `codex`, `gemini`, `claude`, and `speckit` CLIs. (Maps: DOC-REQ-001)
- **FR-002**: The worker image build pipeline MUST keep `speckit` installation in `api_service/Dockerfile` and MUST NOT move `speckit` to a separate image pipeline. (Maps: DOC-REQ-002)
- **FR-003**: Celery producer and worker configuration MUST default to one shared queue named `moonmind.jobs` for AI execution jobs. (Maps: DOC-REQ-003)
- **FR-004**: Worker boot logic MUST enforce `MOONMIND_WORKER_RUNTIME` values `codex`, `gemini`, `claude`, or `universal`, and fail fast on invalid values. (Maps: DOC-REQ-004)
- **FR-005**: Job payload schema MUST support runtime-neutral execution semantics and MUST avoid requiring runtime-specific command fields for baseline execution. (Maps: DOC-REQ-005)
- **FR-006**: Optional targeted runtime execution MUST be supported through payload metadata and processed through universal worker mode without queue changes. (Maps: DOC-REQ-006)
- **FR-007**: Compose/service configuration MUST support both homogeneous and mixed runtime fleets while all runtime workers subscribe to `moonmind.jobs`. (Maps: DOC-REQ-007)
- **FR-008**: Runtime auth material MUST be provided through mounted config/secrets or runtime environment variables and MUST NOT be embedded in built images. (Maps: DOC-REQ-008)
- **FR-009**: Worker startup MUST verify required CLI availability (`codex --version`, `gemini --version`, `claude --version`, `speckit --version`) and MUST block job consumption on failed checks. (Maps: DOC-REQ-009)
- **FR-010**: The migration path MUST support phased cutover with compatibility handling before deprecating legacy queue env vars. (Maps: DOC-REQ-010)
- **FR-011**: Worker telemetry MUST expose runtime-tagged job counters and latencies plus queue wait and execution duration metrics. (Maps: DOC-REQ-011)
- **FR-012**: Final implementation MUST satisfy all source acceptance criteria for single-image + single-queue + env-selected runtime mode with no requeue thrash for runtime-neutral jobs. (Maps: DOC-REQ-012)

### Key Entities *(include if feature involves data)*

- **WorkerRuntimeMode**: Enumerated runtime mode (`codex`, `gemini`, `claude`, `universal`) used by worker boot logic and execution selection.
- **RuntimeNeutralJob**: Queue payload containing repository/workspace context, task objective, constraints, output requirements, and optional runtime hints.
- **RunnerBinding**: Mapping between WorkerRuntimeMode and a concrete runner implementation (`CodexRunner`, `GeminiRunner`, `ClaudeRunner`, `UniversalRunner`).
- **LegacyQueueCompatibility**: Transitional configuration behavior that maps legacy queue settings into single-queue execution during migration.
- **CliHealthSnapshot**: Startup verification result capturing command checks, pass/fail state, and readiness gating.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of runtime worker services consume AI execution jobs from `moonmind.jobs` in a standard deployment.
- **SC-002**: In a mixed-runtime deployment, at least 95% of runtime-neutral jobs start execution within 30 seconds of enqueue under normal load.
- **SC-003**: Startup health checks prevent 100% of job claims when any required CLI check fails.
- **SC-004**: Traceability audit shows every `DOC-REQ-*` mapped to functional requirements, implementation tasks, and validation tasks with zero unmapped requirements.
