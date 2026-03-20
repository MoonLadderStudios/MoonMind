# Feature Specification: OpenClaw streaming external agent

**Feature Branch**: `087-openclaw-adapter`  
**Created**: 2025-03-19  
**Status**: Draft  
**Input**: Fully implement the OpenClaw adapter as described in `docs/ExternalAgents/OpenClawAgentAdapter.md`. Required deliverables include production runtime code changes plus validation tests.

## Source Document Requirements

| ID | Source | Requirement summary |
|----|--------|----------------------|
| DOC-REQ-001 | OpenClaw doc §4.1 | When disabled, OpenClaw must not register and must not be executable; when enabled, gateway URL, default model, and timeout must be configurable via environment/settings. |
| DOC-REQ-002 | OpenClaw doc §4.2 | Gateway token must be supplied via environment (not committed); execution must fail closed if missing when enabled. |
| DOC-REQ-003 | OpenClaw doc §5 | HTTP client must call OpenAI-compatible streaming chat completions, parse SSE `data:` lines and deltas, handle errors without leaking secrets. |
| DOC-REQ-004 | OpenClaw doc §6 | Map `AgentExecutionRequest` into chat messages and aggregate stream text into a canonical `AgentRunResult` for success. |
| DOC-REQ-005 | OpenClaw doc §6.4 | `ProviderCapabilityDescriptor` must declare execution style `polling` vs `streaming_gateway` so orchestration can branch. |
| DOC-REQ-006 | OpenClaw doc §7.1–7.2 | `MoonMind.AgentRun` must use a single long-running `integration.openclaw.execute` activity for streaming providers instead of start/status/fetch_result polling. |
| DOC-REQ-007 | OpenClaw doc §7.3–7.4 | Activity must be registered in the integrations catalog with heartbeat timeout and long start-to-close; worker must expose the handler. |
| DOC-REQ-008 | OpenClaw doc §7.3, §8 | `openclaw` must register in `build_default_registry()` behind the same style of runtime gate as other external providers. |
| DOC-REQ-009 | OpenClaw doc §7.2 | Activity must record Temporal heartbeats during streaming (throttled) for liveness. |
| DOC-REQ-010 | OpenClaw doc §7.2 | Workflow cancellation must tear down the streaming activity so the HTTP connection closes (implicit cancel). |
| DOC-REQ-011 | OpenClaw doc §9 | Automated tests must cover SSE parsing and execution/translation helpers. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run an external task via OpenClaw (Priority: P1)

An operator dispatches an agent step with `agentId=openclaw` and valid configuration. MoonMind completes the run, aggregates streamed assistant output into the task result, and surfaces liveness via Temporal heartbeats.

**Why this priority**: Core integration value.

**Independent Test**: Unit tests for client SSE parsing and execute helper with mocked stream; optional integration test behind env flag.

**Acceptance Scenarios**:

1. **Given** OpenClaw is enabled and token/url are set, **When** `integration.openclaw.execute` runs with a valid `AgentExecutionRequest`, **Then** the activity returns an `AgentRunResult` with summary text from streamed deltas.
2. **Given** OpenClaw is disabled, **When** the registry is built, **Then** `openclaw` is not registered.

---

### User Story 2 - Consistent orchestration with other externals (Priority: P2)

Jules and Codex Cloud continue to use polling activities; OpenClaw uses the streaming branch only when capability says `streaming_gateway`.

**Why this priority**: Avoid regressions.

**Independent Test**: Workflow unit tests or dispatch tests still pass for existing providers.

**Acceptance Scenarios**:

1. **Given** `agentId=jules`, **When** `MoonMind.AgentRun` runs, **Then** it uses start/status/fetch_result as today.

---

### Edge Cases

- Malformed SSE lines are skipped without failing the whole run unless the HTTP status is an error.
- Empty stream yields a bounded failure or empty summary per product choice (document in plan: prefer failure_class integration_error with summary explaining no output).
- Missing token when enabled: fail fast with clear error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** (maps DOC-REQ-001, DOC-REQ-008): System MUST gate OpenClaw registration and execution on explicit enablement and required configuration.
- **FR-002** (maps DOC-REQ-002): System MUST read `OPENCLAW_GATEWAY_TOKEN` from the environment for worker execution and MUST NOT embed secrets in code.
- **FR-003** (maps DOC-REQ-003, DOC-REQ-009, DOC-REQ-010): System MUST stream chat completions to completion, emit heartbeats during the stream, and stop when the activity is cancelled.
- **FR-004** (maps DOC-REQ-004): System MUST translate `AgentExecutionRequest` fields into provider messages and map successful completion to `AgentRunResult`.
- **FR-005** (maps DOC-REQ-005, DOC-REQ-006): System MUST extend provider capabilities with execution style and MUST branch `MoonMind.AgentRun` for `streaming_gateway` providers.
- **FR-006** (maps DOC-REQ-007): System MUST register `integration.openclaw.execute` in the Temporal activity catalog and runtime bindings with appropriate timeouts and heartbeat policy.
- **FR-007** (maps DOC-REQ-011): System MUST add automated tests for OpenClaw client parsing and core translation/execution logic.

### Key Entities

- **OpenClaw execution profile**: Environment-backed URL, model id, timeout, token presence.
- **Stream chunk**: Incremental text delta from SSE; combined into final assistant output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a mocked HTTP stream, OpenClaw client unit tests pass and complete in under 30 seconds locally.
- **SC-002**: `validate-implementation-scope.sh --check tasks --mode runtime` passes for this feature branch.
- **SC-003**: Existing Jules/Codex external adapter unit tests remain green.

## Assumptions

- OpenClaw gateway speaks standard OpenAI SSE chat completions as assumed in the design doc.
- First streaming provider is `openclaw` only; execution style is derived from the adapter capability descriptor.
