# Feature Specification: Generic External Agent Adapter Pattern

**Feature Branch**: `082-external-adapter-pattern`  
**Created**: 2026-03-17  
**Status**: Draft  
**Input**: User description: "Implement 2.3 from docs/MoonMindRoadmap.md — Generic external-agent adapter pattern — Designed in ExternalAgentIntegrationSystem.md, not generalized in code"

## Source Document Requirements

Extracted from `docs/ExternalAgents/ExternalAgentIntegrationSystem.md`:

| Requirement ID | Source Section | Requirement Summary |
|---|---|---|
| DOC-REQ-001 | §3, Layer 3 | Every external provider MUST implement the shared `AgentAdapter` contract via one universal external-agent adapter base class. |
| DOC-REQ-002 | §5 | The base class MUST validate that `agent_kind == "external"` and that the agent_id matches the accepted provider set. |
| DOC-REQ-003 | §5 | The base class MUST inject MoonMind correlation metadata consistently (correlationId, idempotencyKey). |
| DOC-REQ-004 | §5 | The base class MUST apply stable in-memory idempotency caching per activity attempt. |
| DOC-REQ-005 | §5 | The base class MUST normalize common metadata fields: providerStatus, normalizedStatus, externalUrl, callback capability hints. |
| DOC-REQ-006 | §5 | The base class MUST centralize best-effort cancel semantics for providers without native cancellation. |
| DOC-REQ-007 | §5 | Provider subclasses MUST only override provider-specific parts: request translation, status normalization, result extraction, cancel behavior. |
| DOC-REQ-008 | §5 | The base class MUST provide overridable hooks `do_start`, `do_status`, `do_fetch_result`, `do_cancel`. |
| DOC-REQ-009 | §5, Phase C | An explicit provider capability descriptor MUST declare: `supports_callbacks`, `supports_cancel`, `supports_result_fetch`, `provider_name`, `default_poll_hint_seconds`. |
| DOC-REQ-010 | §5, Phase C | The workflow layer MUST consume capability descriptors rather than inferring provider behavior from scattered implementation details. |
| DOC-REQ-011 | §7, Phase E | Adding a new external provider MUST require only: settings, schemas/client, provider adapter subclass, registry registration, optional tooling — without changing `MoonMind.Run` or `MoonMind.AgentRun`. |
| DOC-REQ-012 | §4 | `BaseExternalAgentAdapter` MUST be documented and exported as a first-class public API from the adapters package. |
| DOC-REQ-013 | §7, Phase A | External-agent documentation MUST use consistent vocabulary: "Universal External Agent Stack", "provider transport", "universal external agent adapter", "provider-specific adapter implementation". |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - New Provider Onboarding (Priority: P1)

A developer wants to integrate a new external agent provider (e.g., a hypothetical "Devin" provider). They consult the developer guide, create a settings module, a client, and a subclass of `BaseExternalAgentAdapter`, register it in the registry, add Temporal activities, and the new provider works end-to-end with `MoonMind.AgentRun` — without modifying any orchestration code.

**Why this priority**: Proves the universal pattern is complete and usable. This is the core value proposition of the feature.

**Independent Test**: Can be validated by confirming a stub/mock provider can be added with only subclass + registration + activities, and that existing unit tests for the base class already cover the shared contract.

**Acceptance Scenarios**:

1. **Given** a new provider adapter subclass that extends `BaseExternalAgentAdapter`, **When** the developer implements only `do_start`, `do_status`, `do_fetch_result`, `do_cancel`, and `provider_capability`, **Then** all shared validation, idempotency, correlation metadata, and normalized metadata population work automatically.
2. **Given** the new adapter is registered in `ExternalAdapterRegistry`, **When** `MoonMind.AgentRun` receives a request for the new provider, **Then** the workflow routes through `integration.{provider}.start/status/fetch_result/cancel` activities without any changes to workflow code.

---

### User Story 2 - Capability-Aware Workflow Polling (Priority: P2)

The `MoonMind.AgentRun` workflow currently hardcodes a fallback poll interval of 10 seconds. With the provider capability descriptor in place, the workflow should use `defaultPollHintSeconds` from the capability descriptor, allowing each provider to declare its preferred polling cadence.

**Why this priority**: Makes the capability descriptor actively useful rather than just a passive data structure.

**Independent Test**: Can be tested by verifying that the `AgentRunHandle.poll_hint_seconds` field is populated from the capability descriptor by the base class.

**Acceptance Scenarios**:

1. **Given** a provider declares `defaultPollHintSeconds=30`, **When** the base class builds an `AgentRunHandle`, **Then** `poll_hint_seconds` is set to 30 (unless the provider overrides it explicitly).
2. **Given** a provider declares `supportsCancel=False`, **When** `cancel()` is called, **Then** the base class returns a best-effort fallback status without calling the provider.

---

### User Story 3 - Codex Cloud Temporal Activity Integration (Priority: P2)

Codex Cloud has an adapter (`CodexCloudAgentAdapter`) but lacks Temporal activities (`integration.codex_cloud.*`). Adding these activities following the same pattern as Jules proves the universal adapter architecture and makes Codex Cloud fully operational in the workflow layer.

**Why this priority**: Without Temporal activities, Codex Cloud cannot participate in `MoonMind.AgentRun` — the adapter exists but isn't wired into durable workflows.

**Independent Test**: Can be verified by checking that `codex_cloud_activities.py` follows the same 4-activity pattern as `jules_activities.py` and that existing unit tests for the adapter pass.

**Acceptance Scenarios**:

1. **Given** Codex Cloud is enabled and configured, **When** `MoonMind.AgentRun` receives a request for `codex_cloud`, **Then** the workflow routes through `integration.codex_cloud.start/status/fetch_result/cancel`.
2. **Given** the registry contains both Jules and Codex Cloud, **When** the activity catalog is loaded, **Then** both providers' activities are registered.

---

### User Story 4 - Developer Documentation (Priority: P3)

A developer new to MoonMind can read a step-by-step guide explaining how to add a new external agent provider, including all required files, the adapter subclass contract, activity registration, and testing patterns.

**Why this priority**: Documentation ensures the pattern is sustainable and doesn't require tribal knowledge.

**Independent Test**: Can be verified by reading the guide and confirming it covers all required steps.

**Acceptance Scenarios**:

1. **Given** the developer guide exists, **When** a developer follows the steps, **Then** they can add a complete provider integration with only adapter-specific code.

---

### Edge Cases

- What happens when a provider's `do_cancel` raises an unexpected exception? The base should not crash; it should return a safe fallback status.
- What happens when `provider_capability.supportsCancel` is `False` but `cancel()` is called? The base should return a fallback status without calling `do_cancel`.
- What happens when the idempotency cache sees a duplicate key but the request metadata differs? The cache should return the original handle (first-writer-wins).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** (DOC-REQ-001, DOC-REQ-008): System MUST provide `BaseExternalAgentAdapter` as an abstract base class with overridable `do_start`, `do_status`, `do_fetch_result`, `do_cancel` hooks.
- **FR-002** (DOC-REQ-002): Base class MUST validate `agent_kind == "external"` and `agent_id` membership before delegating to hooks.
- **FR-003** (DOC-REQ-003): Base class MUST inject `moonmind.correlationId` and `moonmind.idempotencyKey` into provider metadata on every `start()`.
- **FR-004** (DOC-REQ-004): Base class MUST maintain an in-memory idempotency cache keyed by `idempotency_key`, returning cached handles for duplicate start requests.
- **FR-005** (DOC-REQ-005): Base class MUST provide `build_handle`, `build_status`, `build_result` helpers that populate `providerStatus`, `normalizedStatus`, `externalUrl` consistently.
- **FR-006** (DOC-REQ-006): Base class MUST provide a best-effort cancel fallback when `provider_capability.supportsCancel` is `False`, returning an `intervention_requested` status without calling `do_cancel`.
- **FR-007** (DOC-REQ-009): Each provider adapter MUST expose a `provider_capability` property returning a `ProviderCapabilityDescriptor`.
- **FR-008** (DOC-REQ-010): `build_handle` MUST automatically populate `poll_hint_seconds` from `provider_capability.defaultPollHintSeconds` when not explicitly set by the provider.
- **FR-009** (DOC-REQ-011): Adding a new external provider MUST require only: settings, client, adapter subclass, registry registration, and Temporal activities — no changes to `MoonMind.Run` or `MoonMind.AgentRun`.
- **FR-010** (DOC-REQ-012): `BaseExternalAgentAdapter` MUST be exported from the `moonmind.workflows.adapters` package `__init__.py`.
- **FR-011** (DOC-REQ-011): Codex Cloud MUST have Temporal activities (`integration.codex_cloud.start/status/fetch_result/cancel`) following the same pattern as Jules.
- **FR-012** (DOC-REQ-013): A developer guide MUST document the step-by-step process for adding a new external agent provider.

### Key Entities

- **BaseExternalAgentAdapter**: Abstract base class implementing shared external-agent lifecycle logic.
- **ProviderCapabilityDescriptor**: Frozen Pydantic model declaring what a provider supports at runtime.
- **ExternalAdapterRegistry**: Registry mapping agent IDs to adapter factories with runtime gating.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All existing unit tests for `BaseExternalAgentAdapter`, `JulesAgentAdapter`, `CodexCloudAgentAdapter`, and `ExternalAdapterRegistry` continue to pass.
- **SC-002**: A new stub provider can be created using only a subclass of `BaseExternalAgentAdapter` plus registry registration, verified by unit test.
- **SC-003**: `poll_hint_seconds` is automatically populated in `AgentRunHandle` when built via the base class.
- **SC-004**: Calling `cancel()` on a provider with `supportsCancel=False` returns an `intervention_requested` status without provider invocation.
- **SC-005**: Codex Cloud activities exist and are registered in the activity catalog.
- **SC-006**: Developer guide covers all required steps for adding a new external provider.
