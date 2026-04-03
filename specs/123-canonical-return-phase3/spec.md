# Feature Specification: Canonical Return — Phase 3 (Managed Runtime Activities)

**Feature Branch**: `123-canonical-return-phase3`
**Created**: 2026-04-02
**Status**: Draft
**Source**: `docs/tmp/010-CanonicalReturnPlan.md` Phase 3

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Managed status poll yields typed canonical status (Priority: P1)

An operator's workflow polls `agent_runtime.status` for a running managed agent run. The workflow expects to receive a well-typed `AgentRunStatus` value directly, not a dict that must be repaired, coerced, or key-mapped before use.

**Why this priority**: `agent_runtime.status` is called every poll cycle for every managed run. Returning a raw dict is the most frequently exercised non-canonical path remaining after Phase 2.

**Independent Test**: Can be fully tested by calling `agent_runtime_status` in a unit test with a mock store and asserting the return type is `AgentRunStatus` with sensible field values.

**Acceptance Scenarios**:

1. **Given** a managed run record with status `running`, **When** `agent_runtime.status` is invoked, **Then** the result is a typed `AgentRunStatus` with `status == "running"` and `agentKind == "managed"`.
2. **Given** a managed run record with status `completed`, **When** `agent_runtime.status` is invoked, **Then** the result is a typed `AgentRunStatus` with `status == "completed"`.
3. **Given** no run record exists in the store, **When** `agent_runtime.status` is invoked, **Then** the result is a typed `AgentRunStatus` with `status == "running"` (optimistic stub).
4. **Given** a managed run record with status `failed`, **When** `agent_runtime.status` is invoked, **Then** the result is a typed `AgentRunStatus` with `status == "failed"` and `metadata` containing the `runtimeId`.

---

### User Story 2 — Managed result fetch yields typed canonical result (Priority: P1)

An operator's workflow calls `agent_runtime.fetch_result` when a run reaches a terminal state. The workflow expects to receive a typed `AgentRunResult` immediately usable for downstream publication logic without dict-to-model coercion.

**Why this priority**: `agent_runtime.fetch_result` feeds directly into proposal generation and artifact publication. Type safety here prevents entire classes of key-aliasing bugs (e.g., `failureClass` vs `failure_class`).

**Independent Test**: Can be tested by calling `agent_runtime_fetch_result` with a store containing terminal records and asserting the return value is an `AgentRunResult` with correct fields.

**Acceptance Scenarios**:

1. **Given** a completed managed run, **When** `agent_runtime.fetch_result` is invoked, **Then** the result is a typed `AgentRunResult` with `failure_class is None`.
2. **Given** a failed managed run, **When** `agent_runtime.fetch_result` is invoked, **Then** the result is a typed `AgentRunResult` with the correct `failure_class` and `summary`.
3. **Given** a timed-out managed run, **When** `agent_runtime.fetch_result` is invoked, **Then** the result is a typed `AgentRunResult` with `failure_class == "timeout"` (or adapter-defined equivalent).
4. **Given** a canceled managed run, **When** `agent_runtime.fetch_result` is invoked, **Then** the result is a typed `AgentRunResult` indicating cancellation without null-pointer behavior.

---

### User Story 3 — Managed cancel yields typed canonical status (Priority: P2)

An operator triggers cancellation of a running managed agent run. The result of `agent_runtime.cancel` is a typed `AgentRunStatus` with `status == "canceled"`, not `None`, allowing the calling workflow to observe the post-cancel state.

**Why this priority**: Returning `None` from cancel is inconsistent with the external provider cancel contract (`AgentRunStatus`). Fixing it makes workflow cancel-path code uniform.

**Independent Test**: Can be tested by calling `agent_runtime_cancel` and asserting the return type is `AgentRunStatus` with `status == "canceled"`.

**Acceptance Scenarios**:

1. **Given** a managed run with a supervisor, **When** `agent_runtime.cancel` is invoked, **Then** the result is a typed `AgentRunStatus` with `status == "canceled"` and `agentKind == "managed"`.
2. **Given** no supervisor is configured, **When** `agent_runtime.cancel` is invoked and the store is updated, **Then** the result is still a typed `AgentRunStatus` (derived from store or synthetic).
3. **Given** cancellation of an unknown run, **When** `agent_runtime.cancel` is invoked, **Then** the result is a typed `AgentRunStatus` with `status == "canceled"` (best-effort).

---

### User Story 4 — Malformed managed activity inputs are rejected at the boundary (Priority: P2)

When the workflow sends a malformed status or fetch-result request (e.g., missing `run_id`), the activity raises a structured error at the boundary rather than silently proceeding with degraded behavior.

**Why this priority**: Fail-fast at the adapter/activity boundary is a constitution requirement (Principle VI — Clarity over Cleverness, Principle IX — Resilience).

**Independent Test**: Can be tested by sending `None` or a dict without `run_id` and asserting a `TemporalActivityRuntimeError` is raised.

**Acceptance Scenarios**:

1. **Given** a missing `run_id` in the request, **When** `agent_runtime.status` is invoked, **Then** a `TemporalActivityRuntimeError` is raised.
2. **Given** a missing `run_id` in the request, **When** `agent_runtime.fetch_result` is invoked, **Then** a `TemporalActivityRuntimeError` is raised.

---

### Edge Cases

- What happens when both the supervisor and the store are unavailable for a cancel call?
- How does the system handle a `publish_artifacts` call with a typed `AgentRunResult` (vs. the existing dict/model mixed path)?
- How does Gemini rate-limit enrichment behave when `fetch_result` returns a typed model instead of a dict?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `agent_runtime.status` MUST return a typed `AgentRunStatus` (not `dict[str, Any]`).
- **FR-002**: `agent_runtime.fetch_result` MUST return a typed `AgentRunResult` (not `dict[str, Any]`).
- **FR-003**: `agent_runtime.cancel` MUST return a typed `AgentRunStatus` (not `None`).
- **FR-004**: `agent_runtime.publish_artifacts` MUST accept and return a typed `AgentRunResult` (not `Any`).
- **FR-005**: `agent_runtime.launch` MUST remain an internal launch-support activity; the `ManagedRunRecord` dict payload it returns is acceptable for this phase (launch semantics differ from status/result/cancel — it returns record state, not a run handle/status).
- **FR-006**: All managed runtime state normalization (store status → `AgentRunState`, error → `failure_class`) MUST occur inside the activity or adapter before returning, never in workflow code.
- **FR-007**: Gemini rate-limit enrichment logic MUST continue to work correctly when `fetch_result` returns a typed model.
- **FR-008**: Large runtime outputs MUST remain in artifact refs; output_refs in `AgentRunResult` MUST be refs, not inline content.
- **FR-009**: Rate-limit and cooldown metadata MUST flow through `AgentRunResult.metadata` without requiring workflow-side repair.
- **FR-010**: Existing unit tests for `agent_runtime.fetch_result` MUST continue to produce the same field values (e.g., `failure_class`, `summary`, `provider_error_code`). Test assertion style WILL migrate from dict-key access (`result["failureClass"]`) to typed attribute access (`result.failure_class`) to match the new typed return contract. Field values are identical; only the access style changes.
- **FR-011**: New tests MUST cover: running state, terminal success, terminal failure, canceled, timed out, and malformed input rejection for managed runtime activities.

### Key Entities

- **AgentRunStatus**: Canonical workflow-facing status contract (`runId`, `agentKind`, `agentId`, `status`, `metadata`).
- **AgentRunResult**: Canonical workflow-facing result contract (`summary`, `output_refs`, `failure_class`, `provider_error_code`, `metadata`).
- **ManagedRunRecord**: Internal store record; MUST NOT cross the workflow boundary directly.
- **ManagedRunStore**: In-process state store; consulted by activities, never surfaced to workflows.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `agent_runtime.status`, `agent_runtime.fetch_result`, and `agent_runtime.cancel` return typed Pydantic models, verifiable by `isinstance` checks in unit tests.
- **SC-002**: All existing `test_agent_runtime_fetch_result.py` tests pass without changing their field-value assertions.
- **SC-003**: New tests for status (running, failed, completed, missing record), fetch_result (success, failure, timeout, cancel), cancel (with/without supervisor), and malformed input are all green.
- **SC-004**: `./tools/test_unit.sh` passes with no regressions across the full test suite.
- **SC-005**: No `dict[str, Any]` return annotation remains on `agent_runtime_status`, `agent_runtime_fetch_result`, or `agent_runtime_cancel` in `activity_runtime.py`.
