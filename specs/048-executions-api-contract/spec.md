# Feature Specification: Executions API Contract Runtime Delivery

**Feature Branch**: `048-executions-api-contract`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/Api/ExecutionsApiContract.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes plus validation tests; docs/spec-only output is not acceptable.  
**Source Document**: `docs/Api/ExecutionsApiContract.md` (Last Updated: 2026-03-06)

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Api/ExecutionsApiContract.md` §1, §2.3-2.4 (lines 14-22, 49-62) | `/api/executions` must be delivered as an execution-oriented adapter API for Temporal-managed work, with contract behavior stable across backing implementation changes. |
| DOC-REQ-002 | `docs/Api/ExecutionsApiContract.md` §4.2-4.3, §5.1-5.2 (lines 93-128) | `workflowId` must be the canonical durable execution identifier, `runId` must remain non-durable detail/debug data, JSON payloads must use camelCase, and this API must not expose `taskId` directly. |
| DOC-REQ-003 | `docs/Api/ExecutionsApiContract.md` §5, §6 (lines 111-161) | The API surface must provide create, list, describe, update, signal, and cancel lifecycle routes, all requiring authenticated users with owner/admin authorization and non-disclosing cross-owner fetch/control behavior. |
| DOC-REQ-004 | `docs/Api/ExecutionsApiContract.md` §7 (lines 167-200) | Supported `workflowType`, domain `state`, and returned `temporalStatus` values must match the documented allowed sets and close-status mapping. |
| DOC-REQ-005 | `docs/Api/ExecutionsApiContract.md` §8.1, §8.3 (lines 206-275) | Responses must use the documented `ExecutionModel` and `ExecutionListResponse` shapes, including required fields, opaque page tokens, `count`, and `countMode`. |
| DOC-REQ-006 | `docs/Api/ExecutionsApiContract.md` §8.2 (lines 226-264) | Execution responses must include baseline search attributes and memo keys, treat these objects as extensible opaque JSON, and target real owner metadata instead of placeholder ownership values. |
| DOC-REQ-007 | `docs/Api/ExecutionsApiContract.md` §9 (lines 279-366) | Create requests must validate the documented fields and rules, apply owner/type/idempotency deduplication, initialize lifecycle metadata, and return `201 Created` with `ExecutionModel` or documented validation/auth failures. |
| DOC-REQ-008 | `docs/Api/ExecutionsApiContract.md` §10 (lines 370-467) | List requests must support documented filters, owner scoping, ordering, pagination, and count semantics, including the rule that non-exact counts are not authoritative for precise totals. |
| DOC-REQ-009 | `docs/Api/ExecutionsApiContract.md` §11 (lines 471-494) | Describe requests must fetch by `workflowId`, return `ExecutionModel` on success, and return `execution_not_found` when the record is missing or hidden by ownership rules. |
| DOC-REQ-010 | `docs/Api/ExecutionsApiContract.md` §12 (lines 498-602) | Update requests must support `UpdateInputs`, `SetTitle`, and `RequestRerun` with the documented request fields, response body, narrow idempotency behavior, terminal-state behavior, and validation/error rules. |
| DOC-REQ-011 | `docs/Api/ExecutionsApiContract.md` §13 (lines 606-697) | Signal requests must support `ExternalEvent`, `Approve`, `Pause`, and `Resume` with documented payload requirements, lifecycle effects, `202 Accepted` success responses, and terminal-state rejection behavior. |
| DOC-REQ-012 | `docs/Api/ExecutionsApiContract.md` §14 (lines 701-751) | Cancel requests must support optional reason/graceful inputs, documented graceful vs forced termination semantics, unchanged terminal returns, and ownership-scoped not-found behavior. |
| DOC-REQ-013 | `docs/Api/ExecutionsApiContract.md` §15 (lines 755-783) | Structured domain errors must use the documented `detail.code` and `detail.message` shape with the known domain error codes, while framework validation failures may still occur outside the stable domain contract. |
| DOC-REQ-014 | `docs/Api/ExecutionsApiContract.md` §16, §19 (lines 787-812, 882-892) | Migration compatibility must preserve `/api/executions` as the execution-oriented adapter, allow task-oriented surfaces to remain user-facing, preserve the `taskId == workflowId` bridge in compatibility adapters, and keep the external execution JSON shape stable. |
| DOC-REQ-015 | Task objective runtime scope guard | Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. |

### Explicitly Out of Scope Source Items

- `docs/Api/ExecutionsApiContract.md` §17 is labeled non-contract explanatory material, so it informs implementation context but does not add separate acceptance requirements for this runtime spec.
- `docs/Api/ExecutionsApiContract.md` §18 defines future contract-governance rules for later revisions; that process requirement is not a runtime behavior deliverable for this implementation slice.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start and Inspect Owned Executions (Priority: P1)

As an authenticated MoonMind user, I can create, list, and inspect my executions through `/api/executions` so I can reliably track Temporal-backed work by durable execution identity.

**Why this priority**: The execution API is only useful if callers can create and read execution records with stable identity, lifecycle state, and owner-scoped visibility.

**Independent Test**: Create both supported workflow types, then verify create/list/describe responses expose the documented fields, initialize lifecycle metadata, enforce owner scoping, and keep `workflowId` as the durable identifier.

**Acceptance Scenarios**:

1. **Given** an authenticated non-admin caller submits a valid create request, **When** the execution starts, **Then** the API returns `201 Created` with an `ExecutionModel` containing a new `workflowId`, current `runId`, `state=initializing`, baseline search attributes, and baseline memo fields.
2. **Given** a non-admin caller lists executions without `ownerId`, **When** the request succeeds, **Then** results contain only that caller's executions ordered by `updatedAt` descending and `workflowId` descending as the tiebreaker.
3. **Given** a caller fetches an execution by `workflowId`, **When** the execution is hidden by ownership rules or absent, **Then** the API returns `execution_not_found` instead of disclosing another user's record.

---

### User Story 2 - Control a Running Execution Safely (Priority: P1)

As an owner or admin, I can update, signal, rerun, and cancel an execution through defined lifecycle contracts so runtime behavior remains predictable and testable.

**Why this priority**: Update, signal, and cancel semantics are the core mutable behaviors of the execution lifecycle and drive both UI controls and integrations.

**Independent Test**: Against a running execution, exercise `UpdateInputs`, `SetTitle`, `RequestRerun`, `ExternalEvent`, `Approve`, `Pause`, `Resume`, graceful cancel, and forced termination; verify accepted/rejected responses, lifecycle effects, terminal behavior, and ownership enforcement.

**Acceptance Scenarios**:

1. **Given** a running owned execution, **When** the caller submits a valid update request, **Then** the response returns documented `accepted`, `applied`, and `message` fields and applies immediately, at the next safe point, or through continue-as-new according to the contract.
2. **Given** a running owned execution, **When** the caller sends supported signals with required payload fields, **Then** the returned `ExecutionModel` reflects the documented lifecycle effects for external events, approvals, pause, and resume.
3. **Given** an execution is already terminal, **When** the caller sends update, signal, or cancel requests, **Then** update returns `accepted=false`, signal returns `signal_rejected`, and cancel returns the unchanged terminal execution body.

---

### User Story 3 - Integrate During Migration Without Contract Drift (Priority: P2)

As a dashboard or integration client, I can rely on stable execution-shaped responses during the Temporal migration even when task-oriented surfaces remain user-facing.

**Why this priority**: The migration succeeds only if the execution API stays stable while compatibility adapters and backing stores evolve.

**Independent Test**: Verify response shapes and identifiers remain stable for execution API consumers while compatibility adapters continue mapping execution records into task-oriented views using `taskId == workflowId`.

**Acceptance Scenarios**:

1. **Given** task-oriented surfaces remain active during migration, **When** they adapt execution data, **Then** `workflowId` remains the canonical durable identity and compatibility adapters preserve the fixed identifier bridge `taskId == workflowId`.
2. **Given** list responses return `countMode=estimated_or_unknown` in a future-compatible path, **When** the client receives the response, **Then** it does not treat the count as an authoritative precise total.
3. **Given** the backing read path changes from projection rows to a different adapter strategy, **When** clients call `/api/executions`, **Then** the external JSON contract remains stable.

### Edge Cases

- `workflowType=MoonMind.ManifestIngest` is requested without `manifestArtifactRef`.
- A non-admin caller provides another user's `ownerId` filter or tries to control another user's execution.
- `nextPageToken` is malformed or `pageSize` falls outside the allowed `1..200` range.
- An update request is a no-op, targets a terminal execution, or reuses only the most recent idempotency key.
- A signal request omits required payload fields or targets a terminal execution.
- Forced termination must map to `state=failed` with `closeStatus=terminated`, while graceful cancel maps to `state=canceled`.
- Continue-as-new or rerun allocates a new `runId` while preserving the same `workflowId`.
- Responses must tolerate additional search attribute or memo keys without breaking documented baseline keys.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose `POST /api/executions`, `GET /api/executions`, `GET /api/executions/{workflowId}`, `POST /api/executions/{workflowId}/update`, `POST /api/executions/{workflowId}/signal`, and `POST /api/executions/{workflowId}/cancel` as the documented execution lifecycle surface for Temporal-managed work. (Maps: DOC-REQ-001, DOC-REQ-003)
- **FR-002**: The system MUST require authenticated MoonMind users for all `/api/executions` routes and enforce owner/admin authorization rules, including `403` for unauthorized list scope and `404` for hidden direct fetch/control operations. (Maps: DOC-REQ-003)
- **FR-003**: The system MUST treat `workflowId` as the canonical durable execution identifier, MUST keep `runId` as non-durable run detail, MUST use camelCase JSON fields, and MUST keep `taskId` out of the direct execution API surface. (Maps: DOC-REQ-002)
- **FR-004**: The system MUST support only the documented `workflowType` values, domain `state` values, and `temporalStatus` mapping from close status, including the rule that continued-as-new is not exposed as a distinct `temporalStatus`. (Maps: DOC-REQ-004)
- **FR-005**: The system MUST return the documented `ExecutionModel` fields for create, describe, signal, and cancel operations and MUST return `ExecutionListResponse` with `items`, opaque `nextPageToken`, optional `count`, and `countMode`. (Maps: DOC-REQ-005)
- **FR-006**: The system MUST populate baseline search attributes (`mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`) and baseline memo fields (`title`, `summary`), MUST keep both objects extensible for additional safe keys, and MUST target real owner metadata rather than placeholder ownership values in the contract-compliant path. (Maps: DOC-REQ-006)
- **FR-007**: The system MUST validate create requests against supported workflow types, required manifest references, small JSON-serializable initial parameters, and artifact-reference semantics; successful creates MUST deduplicate by `(ownerId, workflowType, idempotencyKey)` when a key is supplied. (Maps: DOC-REQ-007)
- **FR-008**: The system MUST initialize successful creates with a new `workflowId`, current `runId`, `state=initializing`, materialized baseline metadata, and `201 Created` plus `ExecutionModel`. (Maps: DOC-REQ-007)
- **FR-009**: The system MUST support list filtering by `workflowType`, `state`, and admin-capable `ownerId`, MUST enforce `pageSize` bounds of `1..200`, MUST order results by `updatedAt` descending then `workflowId` descending, and MUST treat `nextPageToken` as an opaque cursor. (Maps: DOC-REQ-008)
- **FR-010**: The system MUST include `count` and `countMode` in list responses and MUST enforce the contract rule that non-`exact` counts are not authoritative for precise totals or page counts. (Maps: DOC-REQ-005, DOC-REQ-008)
- **FR-011**: The system MUST support describe-by-`workflowId` responses that return `200 OK` with `ExecutionModel` for visible records and `execution_not_found` for missing or ownership-hidden records. (Maps: DOC-REQ-009)
- **FR-012**: The system MUST support `UpdateInputs`, `SetTitle`, and `RequestRerun` update operations with the documented request fields, update response contract (`accepted`, `applied`, `message`), safe-point vs immediate vs continue-as-new application modes, and rerun behavior that preserves `workflowId` while allocating a new `runId`. (Maps: DOC-REQ-010)
- **FR-013**: The system MUST implement the documented narrow update idempotency behavior by replaying only the most recent matching update key and MUST return `200 OK` with `accepted=false` for terminal execution updates. (Maps: DOC-REQ-010)
- **FR-014**: The system MUST support `ExternalEvent`, `Approve`, `Pause`, and `Resume` signals with documented required payload fields and lifecycle effects, MUST return `202 Accepted` with the post-signal `ExecutionModel`, and MUST reject terminal execution signals with `signal_rejected`. (Maps: DOC-REQ-011)
- **FR-015**: The system MUST support optional cancel body inputs `reason` and `graceful`, MUST implement graceful cancel as `state=canceled` with `closeStatus=canceled`, MUST implement forced termination as `state=failed` with `closeStatus=terminated`, and MUST return unchanged execution bodies when cancel is requested on a terminal execution. (Maps: DOC-REQ-012)
- **FR-016**: The system MUST return structured domain errors using `detail.code` and `detail.message`, support the documented domain error codes, and tolerate framework-level validation errors outside the stable domain-specific error contract. (Maps: DOC-REQ-013)
- **FR-017**: The system MUST preserve `/api/executions` as an execution-oriented adapter surface during migration, MUST allow task-oriented surfaces to remain user-facing, MUST preserve the compatibility bridge `taskId == workflowId` in adapter layers, and MUST keep the external execution JSON contract stable across future backing-read changes. (Maps: DOC-REQ-001, DOC-REQ-014)
- **FR-018**: Delivery for this feature MUST include production runtime code changes that implement the execution lifecycle API contract; docs/spec-only completion is not acceptable. (Maps: DOC-REQ-015)
- **FR-019**: Delivery for this feature MUST include automated validation tests that verify route coverage, ownership behavior, lifecycle transitions, error handling, pagination/count behavior, migration compatibility invariants for `/api/executions`, and machine-verifiable `DOC-REQ-001` through `DOC-REQ-015` traceability coverage. (Maps: DOC-REQ-015)

### Key Entities *(include if feature involves data)*

- **Execution**: One Temporal-backed MoonMind workflow execution exposed through `/api/executions`, identified durably by `workflowId` and currently by `runId`.
- **ExecutionModel**: The canonical execution response payload containing identity, lifecycle state, metadata, artifacts, and timestamps for one execution.
- **ExecutionListResponse**: A paginated collection of execution models plus cursor and count metadata for filtered list results.
- **ExecutionOwnershipScope**: The authorization rules that determine whether a caller can create, list, read, update, signal, or cancel a given execution.
- **ExecutionUpdateRequest**: A lifecycle mutation request for input refs, parameters, title, or rerun intent with idempotency and terminal-behavior rules.
- **ExecutionSignalRequest**: An asynchronous control event carrying a supported signal name, optional payload, and optional payload artifact reference.
- **ExecutionCancelRequest**: An optional-body control request that selects graceful cancel or forced termination and may carry a human-readable reason.
- **CompatibilityAdapter**: A migration layer that can transform execution-shaped data into task-oriented payloads while preserving the fixed identity bridge for Temporal-backed work.

### Assumptions & Dependencies

- `/tasks/*` product APIs and legacy `/orchestrator/*` compatibility routes remain separate surfaces and are not redefined by this feature.
- Artifact upload/download APIs and direct Temporal server APIs remain out of scope for this contract implementation.
- The execution API may continue to use an adapter or projection internally as long as the external contract remains stable.
- Section 17 non-contract notes and Section 18 contract-governance rules inform future work but do not add separate runtime acceptance requirements for this feature slice.

### Non-Goals

- Redefining the `/tasks/*` product API as part of this feature.
- Exposing direct Temporal server APIs to clients.
- Expanding artifact upload/download behavior beyond execution-linked artifact references already defined by the contract.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated validation demonstrates all six documented `/api/executions` routes return JSON responses with the documented success status codes and baseline response fields for valid requests.
- **SC-002**: Automated validation demonstrates 100% of supported create flows initialize `workflowId`, `runId`, lifecycle state, search attributes, memo fields, and timestamps according to the documented create contract.
- **SC-003**: Automated validation demonstrates owner/admin authorization behavior for list, describe, update, signal, and cancel requests, including `403` for unauthorized list scope and `404` for hidden direct-access attempts.
- **SC-004**: Automated validation demonstrates update, signal, and cancel flows honor documented lifecycle effects, terminal-state behavior, idempotency limits, and rerun identity rules in all covered scenarios.
- **SC-005**: Automated validation demonstrates list ordering, pagination, and count behavior follow the documented contract, including opaque cursors and non-authoritative handling when `countMode` is not `exact`.
- **SC-006**: Compatibility validation demonstrates execution responses remain stable while task-oriented adapters preserve the fixed identity bridge `taskId == workflowId`.
- **SC-007**: Completion evidence for this feature includes production runtime code changes plus automated validation tests; docs/spec-only output does not satisfy the feature.
- **SC-008**: Automated validation demonstrates `DOC-REQ-001` through `DOC-REQ-015` remain fully mapped in `contracts/requirements-traceability.md` with non-empty validation strategy coverage for the active feature.
