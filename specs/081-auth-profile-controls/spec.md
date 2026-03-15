# Feature Specification: Auth-Profile and Rate-Limit Controls

**Feature Branch**: `081-auth-profile-controls`
**Created**: 2026-03-15
**Status**: Draft
**Input**: User description: "Implement Phase 5 of docs/Temporal/ManagedAndExternalAgentExecutionModel.md"

## Source Document Requirements

Source: `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` — Phase 5 and Sections 7, 9.

- **DOC-REQ-001**: [Sec 7: ManagedAgentAuthProfile] The system must provide a `ManagedAgentAuthProfile` struct/model with fields: `profile_id`, `runtime_id`, `auth_mode`, `volume_ref`, `account_label`, `max_parallel_runs`, `cooldown_after_429`, `rate_limit_policy`, `enabled`. (Sec 7 — `ManagedAgentAuthProfile`)
- **DOC-REQ-002**: [Sec 11, Phase 5] Auth-profile-based runtime selection: the managed adapter must select the appropriate execution profile at runtime based on `execution_profile_ref` passed in the `AgentExecutionRequest`. (Sec 11 Phase 5 + Sec 3 AgentExecutionRequest)
- **DOC-REQ-003**: [Sec 7: Concurrency Enforcement] Per-profile concurrency limits must be enforced — each auth profile defines `max_parallel_runs`, and the system must reject or queue new run requests when the limit is reached. (Sec 7 Concurrency Enforcement)
- **DOC-REQ-004**: [Sec 7: Concurrency per profile] Concurrency is enforced per auth-profile, not per runtime family (e.g., separate limits for `gemini_oauth_user_a` vs `claude_code_team_profile`). (Sec 7 Concurrency Enforcement, Examples)
- **DOC-REQ-005**: [Sec 11, Phase 5] Provider-specific cooldown/backoff: when a 429 RESOURCE_EXHAUSTED response is received, the profile must enter cooldown for a duration specified by `cooldown_after_429`. (Sec 7 Rules + Sec 11 Phase 5)
- **DOC-REQ-006**: [Sec 7: Rules — Credentials] Raw credentials must never be placed in workflow payloads, artifacts, or logs. Runtime execution requests must reference the auth profile only by `profile_id`. (Sec 7 Rules)
- **DOC-REQ-007**: [Sec 7: Rules — OAuth env shaping] OAuth-mode profiles must shape the execution environment by clearing API-key environment variables so the runtime uses the persisted OAuth home. (Sec 7 Rules)
- **DOC-REQ-008**: [Sec 7: Rules — Credential storage] Auth state for OAuth-based CLIs must be stored in persistent runtime-specific volumes or equivalent durable credential homes. (Sec 7 Rules)
- **DOC-REQ-009**: [Sec 7: Rules — Runtime-specific env] Runtime-specific environment shaping must be supported, including both OAuth mode (clear API keys) and API-key mode (inject key without OAuth home). (Sec 7 Rules)
- **DOC-REQ-010**: [Sec 9: Worker Fleet] Managed-agent execution requires persistent auth volume mounts and provider-specific concurrency controls, which must be accounted for in the activity/worker design even while still on the sandbox fleet. (Sec 9 Migration Note)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Profile-Aware Managed Agent Execution (Priority: P1)

When a managed agent (Gemini CLI, Claude Code, Codex CLI) is launched, the orchestration system automatically selects and leases an available auth profile, applies environment shaping, and enforces concurrency limits so every run operates under proper rate and credential controls.

**Why this priority**: Core correctness requirement — without profile-based execution, agents may collide on shared credentials, exceed provider rate limits, or inadvertently use wrong auth modes.

**Independent Test**: Can be validated by configuring multiple auth profiles for the same runtime family and running parallel managed agent tasks, observing correct profile assignment and environment shaping per run.

**Acceptance Scenarios**:

1. **Given** an `AgentExecutionRequest` with `execution_profile_ref` pointing to an OAuth profile, **When** the managed adapter starts the run, **Then** the correct auth profile is selected, the OAuth volume is mounted, and API-key environment variables are cleared.
2. **Given** an `AgentExecutionRequest` with `execution_profile_ref` pointing to an API-key profile, **When** the managed adapter starts the run, **Then** the API key is injected into the environment and no OAuth home is referenced.
3. **Given** an auth profile with `max_parallel_runs = 1` already at capacity, **When** a second run request arrives for that profile, **Then** the second request is queued or rejected, not allowed to proceed concurrently.

---

### User Story 2 - 429 Cooldown and Profile Failover (Priority: P1)

When a managed agent run encounters a provider rate-limit (HTTP 429), the system automatically sidelines the responsible auth profile for a cooldown period and routes new requests to an available profile or queues them if none are available.

**Why this priority**: Required for production reliability — without cooldown handling, repeated 429s result in wasted runs and provider-side throttling that degrades the whole team's quota.

**Independent Test**: Can be validated by simulating a 429 response and observing that the offending profile enters cooldown, and follow-up requests use an alternate profile or queue correctly.

**Acceptance Scenarios**:

1. **Given** a managed run that receives a 429 response, **When** the run terminates, **Then** the profile is marked as in-cooldown for `cooldown_after_429` seconds and no new runs are assigned to it during cooldown.
2. **Given** all profiles for a runtime are in cooldown, **When** a new run is requested, **Then** it waits durably until a cooldown expires rather than failing immediately.
3. **Given** a cooldown period expires, **When** the next request arrives, **Then** the profile is made available again and the queued request proceeds.

---

### User Story 3 - Credential Isolation and Secret Hygiene (Priority: P1)

Auth profiles are referenced only by `profile_id` throughout the orchestration system. Credentials, tokens, and keys never appear in workflow payloads, task artifacts, logs, or PR comments.

**Why this priority**: Non-negotiable security requirement from the constitution — credential leakage in workflow history or logs is a critical vulnerability.

**Independent Test**: Can be validated by inspecting Temporal workflow history and MoonMind artifact storage for any profile runs and confirming no credential values appear.

**Acceptance Scenarios**:

1. **Given** a managed agent run backed by an OAuth profile, **When** examining the Temporal workflow history and any resulting artifacts, **Then** no token values, cookie strings, or credential data appear — only `profile_id` references.
2. **Given** a managed agent run backed by an API-key profile, **When** examining Temporal history, **Then** the API key value is absent; only the profile ID and volume/env reference appear.

---

### Edge Cases

- What happens when `execution_profile_ref` does not match any registered auth profile? (Fail fast with an actionable error; do not silently fall back to a default profile.)
- What happens when a profile's `enabled` flag is set to `false` during a run already in progress? (The in-progress run completes; the profile is excluded from new assignments immediately.)
- What happens when all profiles for a runtime are disabled or in cooldown indefinitely? (Run fails with a clear terminal status indicating no available profiles; does not hang indefinitely.)
- What happens if `cooldown_after_429` is `0` or unset? (Treat as no cooldown; profile remains immediately available after a 429.)
- What happens if `max_parallel_runs` is `0` or unset? (Treat as unlimited or use a safe default of 1; document the chosen default.)

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define a `ManagedAgentAuthProfile` data structure with at minimum: `profile_id`, `runtime_id`, `auth_mode` (`oauth` | `api_key`), `volume_ref`, `account_label`, `max_parallel_runs`, `cooldown_after_429`, `rate_limit_policy`, `enabled`. (Maps to DOC-REQ-001)
- **FR-002**: The `ManagedAgentAdapter` MUST resolve and apply the specified `execution_profile_ref` from the `AgentExecutionRequest` before launching any managed runtime. (Maps to DOC-REQ-002)
- **FR-003**: The system MUST enforce per-profile concurrency limits: if a profile is at `max_parallel_runs`, new requests for that profile MUST be queued or rejected, not silently over-subscribed. (Maps to DOC-REQ-003)
- **FR-004**: Concurrency limits MUST be tracked per individual auth-profile, not at the runtime-family level. (Maps to DOC-REQ-004)
- **FR-005**: When a managed run encounters a 429 (RESOURCE_EXHAUSTED) response, the system MUST enter a cooldown period for that profile equal to `cooldown_after_429` seconds, during which no new runs are assigned to it. (Maps to DOC-REQ-005)
- **FR-006**: Auth profiles MUST be referenced only by `profile_id` in workflow payloads, artifacts, and logs. Raw credentials MUST NOT appear in any durable workflow state. (Maps to DOC-REQ-006)
- **FR-007**: When an OAuth-mode auth profile is selected, the runtime environment MUST have API-key environment variables explicitly cleared before the managed runtime starts. (Maps to DOC-REQ-007)
- **FR-008**: OAuth credential state MUST be stored in and read from persistent runtime-specific volumes, not injected into environment variables at launch time. (Maps to DOC-REQ-008)
- **FR-009**: The system MUST support environment shaping for both `oauth` and `api_key` auth modes with distinct logic for each mode. (Maps to DOC-REQ-009)
- **FR-010**: The activity/worker hosting managed-agent execution MUST support persistent auth volume mounts and enforce per-profile concurrency limits even while operating on the existing sandbox fleet. (Maps to DOC-REQ-010)
- **FR-011**: The system MUST fail fast with an actionable error when `execution_profile_ref` does not resolve to a known, enabled auth profile (rather than silently using a fallback).
- **FR-012**: The system MUST provide validation tests covering: profile selection, environment shaping (oauth + api_key), concurrency limit enforcement, and 429 cooldown behavior.

### Key Entities

- **ManagedAgentAuthProfile**: Named auth and execution policy record for a managed runtime. Drives profile selection, env shaping, concurrency enforcement, and cooldown tracking.
- **execution_profile_ref**: Reference field on `AgentExecutionRequest` used to look up the target `ManagedAgentAuthProfile` at runtime.
- **AuthProfileConcurrencyState**: Transient runtime state tracking active slot count and cooldown expiry per profile (lives in the managed adapter / concurrency manager, not necessarily in Temporal workflow state).
- **EnvironmentSpec**: The shaped environment dictionary emitted by the auth profile resolver, containing the correct env vars for the selected profile mode.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of managed agent runs successfully resolve and apply an auth profile before the runtime is started.
- **SC-002**: Zero runs exceed the `max_parallel_runs` concurrency limit for any auth profile.
- **SC-003**: 100% of 429 responses correctly trigger a cooldown that prevents new run assignment to the responsible profile for `cooldown_after_429` seconds.
- **SC-004**: Zero credential values (tokens, API keys, OAuth cookies) appear in Temporal workflow history or MoonMind artifact storage across any managed agent run.
- **SC-005**: OAuth-mode runs have zero API-key environment variables present in the shaped runtime environment.
- **SC-006**: All FR-001 through FR-012 are covered by at least one passing automated test.
