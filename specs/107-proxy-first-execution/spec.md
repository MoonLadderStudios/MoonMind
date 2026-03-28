# Feature Specification: Proxy-First Execution Paths

**Feature Branch**: `107-proxy-first-execution`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Proxy-First Execution Paths for MoonMind runtimes"

## Source Document Requirements

- **DOC-REQ-001**: Inventory provider and tool call paths that MoonMind owns directly
- **DOC-REQ-002**: Identify which call paths can switch to proxy-first execution now
- **DOC-REQ-003**: Introduce internal capability or token patterns where a caller needs MoonMind authorization instead of the provider secret
- **DOC-REQ-004**: Keep runtime credential materialization only for third-party executables that genuinely require it
- **DOC-REQ-005**: Document which runtimes remain escape-hatch cases and why

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Secure Proxy-First Execution (Priority: P1)

As a systems operator, I want MoonMind-owned execution environments (such as internal task processing) to proxy LLM requests through MoonMind's `api_service` rather than passing raw `ANTHROPIC_API_KEY` into the remote instances, so that secrets are never unnecessarily spilled to workers.

**Why this priority**: It is the core security mechanism defined by Phase 5, restricting raw credential access.

**Independent Test**: Can be fully tested by launching an agent with proxy-first configuration and verifying that its OS environment does not contain the raw API key, but it can successfully complete an LLM request via a proxy token.

**Acceptance Scenarios**:

1. **Given** a Managed Agent runtime profile supporting `proxy-first` connectivity, **When** I run an agent task, **Then** the worker environment contains a `MOONMIND_PROXY_TOKEN` instead of the raw `PROVIDER_API_KEY`.
2. **Given** an agent using proxy credentials, **When** the agent initiates an API request, **Then** the request routes successfully through the `api_service` proxy endpoint bridging to the actual provider.

---

### User Story 2 - Escape Hatch Runtimes documentation (Priority: P2)

As a platform engineer, I want to clearly document which third-party executables (like standard `codex` CLI) still require the legacy environment variables, so that I understand where security boundaries apply.

**Why this priority**: Required for compliance and ensuring operators understand which runtimes safely use proxy endpoints and which don't.

**Independent Test**: Can be verified by reading output design documents in the `docs/Security/` paths.

**Acceptance Scenarios**:

1. **Given** the system documentation, **When** reading runtime configurations, **Then** there is a clear distinction between proxy-capable runtimes and escape-hatch runtimes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST map DOC-REQ-001 and DOC-REQ-002 by producing an inventory identifying compliant tool paths (e.g. `jules` direct integrations or Python-based managed agents) vs non-compliant ones (e.g. compiled CLI binaries needing native config).
- **FR-002**: The `ManagedAgentAdapter` MUST map DOC-REQ-003 by injecting a proxy base URL and proxy token to the environment instead of resolving `db_encrypted:...` directly into `OPENAI_API_KEY` for supported profiles.
- **FR-003**: The architecture MUST map DOC-REQ-004 by supporting an "escape hatch" flag on Provider Profiles determining whether it requires raw credential injection. 
- **FR-004**: The system MUST map DOC-REQ-005 by delivering architectural documentation summarizing escape-hatch runtime decisions.
- **FR-005**: The `api_service` MUST expose a functional proxy route (e.g., `/api/v1/proxy/anthropic/v1/messages`) that verifies the `MOONMIND_PROXY_TOKEN` and forwards traffic to the provider using the securely held database secret.

### Key Entities

- **Provider Profile**: Needs configuration indicating if `proxy_first` mode is available/enforced.
- **Proxy Token**: An ephemeral, tightly scoped JWT or task-specific token injected to the worker.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The worker process environment variables of a proxy-enabled run do not expose raw AI provider tokens.
- **SC-002**: The `api_service` successfully handles proxy pass-through for completions.
- **SC-003**: 100% of the DOC-REQ-* requirements are covered by architectural documents and code features.
