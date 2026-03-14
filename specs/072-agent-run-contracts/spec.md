# Feature Specification: Agent Runtime Phase 1 Contracts

**Feature Branch**: `072-agent-run-contracts`  
**Created**: 2026-03-14  
**Status**: Draft  
**Input**: User description: "Implement Phase 1 of docs/Temporal/ManagedAndExternalAgentExecutionModel.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §11 "Phase 1 — Formalize Contracts" | Phase 1 delivery must add code and docs for unified agent-run contract types and references. |
| DOC-REQ-002 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §3 "AgentExecutionRequest" | `AgentExecutionRequest` must represent canonical true-agent requests with agent kind/id, profile refs, correlation/idempotency keys, artifact refs, workspace spec, runtime parameters, and control policies. |
| DOC-REQ-003 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §3 "AgentRunHandle" | Start operations must return a normalized `AgentRunHandle` including run identity, agent metadata, status, and poll hints. |
| DOC-REQ-004 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §3 "AgentRunStatus" | Agent run lifecycle state must use explicit normalized statuses with stable terminal-state semantics. |
| DOC-REQ-005 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §3 "AgentRunResult" | Final run results must provide output refs, summary/metrics, diagnostics reference, and normalized failure metadata. |
| DOC-REQ-006 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §3 "Idempotency Requirements" | Side-effecting start-like operations must be idempotent on stable idempotency identity to prevent duplicate run creation. |
| DOC-REQ-007 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §4 "AgentAdapter" | A shared `AgentAdapter` contract must expose `start`, `status`, `fetch_result`, and `cancel` as the common minimum interface. |
| DOC-REQ-008 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §7 "ManagedAgentAuthProfile" + rules | `ManagedAgentAuthProfile` must model runtime auth/execution policy using indirect references without placing raw credentials in workflow payloads/logs/artifacts. |
| DOC-REQ-009 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §7 "Concurrency Enforcement" | Concurrency and cooldown policy must be represented per auth profile (not only per runtime family). |
| DOC-REQ-010 | `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` §8 "Artifact and Log Discipline" | Contracts must keep large payload/log/transcript content out of workflow payloads and use artifact references for durable exchange. |
| DOC-REQ-011 | User runtime scope guard | Delivery must include production runtime code changes plus validation tests (not docs/spec-only output). |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Platform Defines Unified Agent-Run Contracts (Priority: P1)

As a workflow/runtime engineer, I need canonical contract models for agent execution so external and MoonMind-managed runtimes can share one lifecycle envelope.

**Why this priority**: Phase 1 is contract formalization; without these types, later workflow/adapters cannot integrate consistently.

**Independent Test**: Instantiate and validate each contract model with representative payloads for external and managed runs and verify required fields and terminal-state rules.

**Acceptance Scenarios**:

1. **Given** a canonical agent execution payload, **When** it is validated as `AgentExecutionRequest`, **Then** required identity, policy, and artifact-reference fields are accepted and normalized.
2. **Given** run lifecycle data from either adapter path, **When** it is represented as `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`, **Then** status semantics and result fields are consistent and deterministic.

---

### User Story 2 - Runtime Integrations Use One Adapter Interface (Priority: P1)

As a Temporal integration engineer, I need a shared adapter protocol so existing external runtimes can plug in now and managed runtimes can plug in later without changing workflow-facing contracts.

**Why this priority**: A common interface is required in Phase 1 to prevent divergent integration surfaces per provider/runtime.

**Independent Test**: Validate that a concrete external adapter implementation can satisfy the common protocol and produce normalized handle/status/result contracts.

**Acceptance Scenarios**:

1. **Given** an adapter implementation, **When** it is used through the shared interface, **Then** `start`, `status`, `fetch_result`, and `cancel` operations are available and return canonical contract types.
2. **Given** provider-specific raw status and payloads, **When** the adapter returns canonical contracts, **Then** mapped states and result structure match the normalized model.

---

### User Story 3 - Managed Auth Policy Is Contracted Safely (Priority: P2)

As a platform operator, I need managed-agent auth profile contracts so runtime concurrency and cooldown can be enforced safely per profile without leaking credentials.

**Why this priority**: Auth/concurrency policy is foundational for managed runtime safety and required before Phase 5 enforcement implementation.

**Independent Test**: Validate `ManagedAgentAuthProfile` inputs for enabled profiles, per-profile concurrency/cooldown fields, and reject unsafe/invalid values while keeping credential references indirect.

**Acceptance Scenarios**:

1. **Given** a managed auth profile payload, **When** it is validated, **Then** profile identity, runtime mapping, and policy fields are accepted with per-profile limits.
2. **Given** an invalid or unsafe profile payload, **When** validation runs, **Then** contract validation fails without requiring raw credential data fields.

### Edge Cases

- What happens when `AgentRunStatus` receives an unknown or provider-specific state not in the canonical set?
- How does idempotency behave when the same idempotency key is retried with semantically different start inputs?
- How does contract validation handle oversized inline blobs/log text that should be artifact refs?
- What happens when a managed auth profile sets invalid policy values (for example zero `max_parallel_runs` or negative cooldown)?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST deliver production runtime code changes for Phase 1 that formalize unified true-agent execution contracts and include validation tests. (DOC-REQ-001, DOC-REQ-011)
- **FR-002**: The system MUST provide an `AgentExecutionRequest` contract with canonical identity, artifact reference, workspace, parameter, timeout/retry/approval/callback policy, and profile-reference fields for true-agent execution. (DOC-REQ-002)
- **FR-003**: The system MUST provide an `AgentRunHandle` contract returned from start operations, including run identity, agent metadata, current status, and optional poll hint metadata. (DOC-REQ-003)
- **FR-004**: The system MUST provide an `AgentRunStatus` contract with explicit normalized lifecycle states and fixed terminal-state semantics for `completed`, `failed`, `cancelled`, and `timed_out`. (DOC-REQ-004)
- **FR-005**: The system MUST provide an `AgentRunResult` contract that returns output artifact references, summary, metrics, diagnostics reference, and failure classification fields. (DOC-REQ-005)
- **FR-006**: The system MUST enforce idempotent start-like behavior through a stable idempotency identity and reject malformed idempotency inputs. (DOC-REQ-006)
- **FR-007**: The system MUST define a shared `AgentAdapter` interface exposing `start`, `status`, `fetch_result`, and `cancel` operations over canonical contracts. (DOC-REQ-007)
- **FR-008**: The system MUST include an external-agent adapter contract implementation path that maps provider-native fields into canonical contracts without leaking provider-specific schema into workflow-facing types. (DOC-REQ-007)
- **FR-009**: The system MUST provide a `ManagedAgentAuthProfile` contract with named profile identity, runtime binding, auth mode, durable auth reference, and per-profile concurrency/cooldown policy fields. (DOC-REQ-008, DOC-REQ-009)
- **FR-010**: The system MUST ensure contract surfaces store large prompt/context/log/transcript payloads as artifact references and keep workflow payload surfaces compact. (DOC-REQ-010)
- **FR-011**: The system MUST avoid raw credential fields in workflow-facing contract payloads and require indirect references for auth material. (DOC-REQ-008)

### Key Entities *(include if feature involves data)*

- **AgentExecutionRequest**: Canonical request envelope for true agent runtime execution, independent of external vs managed adapter path.
- **AgentRunHandle**: Start acknowledgment payload with normalized run metadata for subsequent status/result/cancel operations.
- **AgentRunStatus**: Canonical lifecycle state model with stable terminal-state behavior.
- **AgentRunResult**: Canonical final result envelope containing artifact references and failure metadata.
- **AgentAdapter**: Shared provider/runtime adapter interface for lifecycle operations.
- **ManagedAgentAuthProfile**: Managed-runtime auth and execution policy contract with per-profile concurrency and cooldown controls.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Contract validation tests cover all new Phase 1 models and pass with 100% success in CI/local unit test runs.
- **SC-002**: At least one concrete external adapter path can round-trip `start -> status -> fetch_result` using the canonical contracts without schema translation errors.
- **SC-003**: Idempotent start behavior returns the same run handle for repeated valid calls sharing one stable idempotency key in automated tests.
- **SC-004**: Contract tests verify that large output/log/transcript values are represented by artifact references instead of inline workflow payload blobs.
