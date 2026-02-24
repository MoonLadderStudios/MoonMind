# Feature Specification: Claude Runtime API-Key Gating

**Feature Branch**: `038-claude-runtime-gate`  
**Created**: February 24, 2026  
**Status**: Draft  
**Input**: User description: "Implement PR 1 from the Claude API-key removal plan: replace legacy Claude auth checks with Anthropic API key gating across worker preflight, task queue validation/default runtime, and dashboard runtime options, including required unit tests and runtime behavior updates. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Worker preflight blocks unsafe Claude modes (Priority: P1)

As a platform operator starting a Claude-specialized or universal worker, I need preflight to fail fast when no Anthropic API key is configured so that the worker never runs in a broken state.

**Why this priority**: Failed workers waste capacity and cause stuck jobs; preventing them is critical before any queue work can proceed.

**Independent Test**: Start the worker CLI in `claude` runtime mode (or with `claude` capability) with and without an API key to verify preflight behavior.

**Acceptance Scenarios**:

1. **Given** a worker launched with `MOONMIND_WORKER_RUNTIME=claude` and no `ANTHROPIC_API_KEY`, **When** preflight runs, **Then** it raises a descriptive error and aborts startup before any task polling begins.
2. **Given** a universal worker whose capabilities include `claude` and `codex`, **When** `ANTHROPIC_API_KEY` is present, **Then** preflight runs `claude --version` verification exactly once and succeeds without invoking any legacy auth helpers.
3. **Given** a universal worker that only advertises `codex`, **When** no Anthropic key exists, **Then** preflight skips all Claude checks and still succeeds so the worker can process non-Claude work.

---

### User Story 2 - Queue rejects unavailable Claude runtimes (Priority: P1)

As an API client submitting automation tasks, I need immediate feedback if I request the Claude runtime while the system lacks a Claude API key so I can choose another runtime instead of submitting unfulfillable jobs.

**Why this priority**: Prevents stranded queue entries and provides deterministic guidance to API consumers.

**Independent Test**: POST `targetRuntime=claude` via the queue API with and without an API key configured and examine the HTTP response.

**Acceptance Scenarios**:

1. **Given** `ANTHROPIC_API_KEY` is unset, **When** a client sends a task payload whose resolved runtime is `claude`, **Then** the API responds with HTTP 400 and the message "targetRuntime=claude requires ANTHROPIC_API_KEY to be configured" without inserting the job into the queue.
2. **Given** `ANTHROPIC_API_KEY` is set, **When** the same payload is submitted, **Then** the API accepts it and enqueues the task normally.
3. **Given** an operator configures `MOONMIND_DEFAULT_TASK_RUNTIME=claude` while no key is defined, **When** the API service boots, **Then** it fails fast with a configuration error so invalid defaults cannot silently strand jobs.

---

### User Story 3 - Dashboard hides unusable runtimes (Priority: P2)

As a dashboard user selecting a runtime for manual task runs, I need the UI to show only runtimes that have satisfied prerequisites so I am never offered Claude unless an Anthropic key is configured.

**Why this priority**: Prevents UX confusion and reduces support requests caused by hidden server-side constraints.

**Independent Test**: Load the dashboard view model with and without an API key and inspect `supportedTaskRuntimes` and `defaultTaskRuntime` values.

**Acceptance Scenarios**:

1. **Given** no Anthropic key exists, **When** the dashboard config endpoint is fetched, **Then** `supportedTaskRuntimes` contains only `codex` and `gemini`, and any default set to `claude` is replaced by the first available runtime.
2. **Given** an Anthropic key is present, **When** the config endpoint is fetched, **Then** `supportedTaskRuntimes` includes `claude`, `codex`, and `gemini` in priority order, and the default honors `claude` if configured.
3. **Given** the UI receives `supportedTaskRuntimes` without `claude`, **When** a user opens the runtime dropdown, **Then** Claude is absent, eliminating the possibility of selecting an unsupported runtime.

### Edge Cases

- API key variables exist but contain only whitespace; the system must treat them as missing and block Claude modes.
- Deployment defines `CLAUDE_API_KEY` (legacy alias) but not `ANTHROPIC_API_KEY`; the system should treat either variable as satisfying the key requirement, preferring `ANTHROPIC_API_KEY` when both exist.
- Workers that dynamically update `MOONMIND_WORKER_CAPABILITIES` at runtime should only require the key if the final capability string contains `claude` (case-insensitive) to avoid false failures.
- Dashboard `MOONMIND_WORKER_RUNTIME` setting might reference an unsupported runtime; the config builder must gracefully fall back without breaking page load.
- In flight tasks queued before the change with `targetRuntime=claude` must remain claimable once a key is provided; the validation applies only at enqueue time.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Worker preflight MUST detect when `MOONMIND_WORKER_RUNTIME="claude"` and fail with a clear error if neither `ANTHROPIC_API_KEY` nor `CLAUDE_API_KEY` contains a non-empty value.
- **FR-002**: Worker preflight MUST require the same key gate whenever `MOONMIND_WORKER_CAPABILITIES` includes `claude`, even if the primary runtime differs.
- **FR-003**: When the key requirement is satisfied, worker preflight MUST verify local Claude tooling by running `claude --version` exactly once; this check MUST NOT run when Claude is not required.
- **FR-004**: Worker preflight MUST remove support for legacy auth-status hooks so no legacy auth binaries are invoked.
- **FR-005**: The queue normalization/enqueue service MUST reject any task whose resolved runtime is `claude` when the key requirement is not met, returning HTTP 400 with a descriptive message and leaving the queue unchanged.
- **FR-006**: The queue service MUST accept Claude tasks when the key is present without needing any legacy auth volume or command prerequisites.
- **FR-007**: Settings validation MUST raise a startup error if `MOONMIND_DEFAULT_TASK_RUNTIME` resolves to `claude` while no key is configured, preventing the API service from starting in an invalid state.
- **FR-008**: Dashboard runtime config (`supportedTaskRuntimes`, `defaultTaskRuntime`) MUST dynamically include Claude only when the key exists and MUST otherwise expose only runtimes that pass the gate.
- **FR-009**: When Claude is disabled, the dashboard MUST ensure the reported default runtime matches one of the supported runtimes, falling back deterministically (e.g., prioritize `codex`, then `gemini`).
- **FR-010**: All new behaviors MUST be covered by automated unit tests spanning worker preflight, queue validation, settings enforcement, and dashboard config serialization.

### Key Entities *(include if feature involves data)*

- **Runtime Gate State**: Derived from `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY`; attributes include `is_claude_enabled` (boolean) and `reason` (string) for error messaging. Consumed by workers, API validation, and dashboard config builders.
- **Task Runtime Descriptor**: Represents the resolved runtime for a queued task, including `requestedRuntime`, `defaultRuntimeSource` (e.g., client payload vs. settings), and `validationErrors` used in HTTP responses.
- **Dashboard Runtime Config**: JSON payload containing `supportedTaskRuntimes` (ordered list) and `defaultTaskRuntime`, derived from runtime gate state plus `MOONMIND_WORKER_RUNTIME` preference.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of worker preflight attempts for Claude-required modes without an Anthropic key terminate before task polling, emitting a human-actionable error message within 2 seconds of startup.
- **SC-002**: Queue submissions requesting `targetRuntime=claude` receive deterministic validation responses within 1 second, with zero tasks accepted when the key is missing and zero valid requests rejected when the key is present during regression testing.
- **SC-003**: Dashboard runtime dropdown never displays Claude when the Anthropic key is absent, as verified by automated view-model tests covering both enabled and disabled states.
- **SC-004**: Settings boot validation blocks any configuration that defaults to a disabled Claude runtime, ensuring API startup fails fast with a clear message in all integration smoke tests.
- **SC-005**: New unit tests cover each gating scenario (worker, queue, dashboard, settings), and the suite passes consistently via `./tools/test_unit.sh`.

## Assumptions & Dependencies

- Either `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` satisfies the key requirement; the system normalizes both to a single gate flag.
- Runtime capability strings use lowercase identifiers (`codex`, `claude`, `gemini`); matching is case-insensitive but stored in lowercase for consistency.
- No additional legacy auth artifacts (volumes, helper scripts) remain within the PR1 scope; their removal is handled in later PRs.
- Dashboard clients already honor the `supportedTaskRuntimes` contract; no frontend changes are necessary beyond consuming updated data.
- CI relies exclusively on `./tools/test_unit.sh`; all new tests will be added under the existing unit test suites referenced in the plan.
