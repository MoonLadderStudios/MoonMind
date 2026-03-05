# Feature Specification: Workflow Type Catalog and Lifecycle

**Feature Branch**: `046-workflow-type-lifecycle`  
**Created**: 2026-03-05  
**Status**: Draft  
**Input**: User description: "Fully implement the Workflow Type Catalog and Lifecycle system as described in docs/Temporal/WorkflowTypeCatalogAndLifecycle.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §2.1-2.2 (lines 28-33) | A dashboard row must represent exactly one Temporal Workflow Execution, and root-level categorization must use Workflow Type only. |
| DOC-REQ-002 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §2.3-2.5 (lines 34-42) | Workflows must orchestrate while side effects and nondeterminism are executed in Activities, and large payloads must be kept outside workflow history via artifact references. |
| DOC-REQ-003 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §2.4, §6 (lines 37-39, 167-261) | Edits must be implemented as Updates, while asynchronous external/human events must be implemented as Signals. |
| DOC-REQ-004 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §3.1 (lines 52-62) | Workflow Type names must follow `MoonMind.*`, remain stable, and only include materially distinct behavior types. |
| DOC-REQ-005 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §3.2 (lines 65-74) | Workflow IDs must follow `mm:<ulid-or-uuid>`, be safe for API exposure, and reuse the same Workflow ID for rerun/restart through Continue-As-New when appropriate. |
| DOC-REQ-006 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §4.1 (lines 87-92) | v1 catalog must include `MoonMind.Run` and `MoonMind.ManifestIngest` with distinct responsibilities. |
| DOC-REQ-007 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §5.1 (lines 102-127) | `mm_state` must be a single keyword Search Attribute with fixed allowed values and terminal mapping consistent with Temporal close status. |
| DOC-REQ-008 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §5.2 (lines 134-163) | Visibility schema must include required Search Attributes (`mm_owner_id`, `mm_state`, `mm_updated_at`) and required Memo fields (`title`, `summary`) with small human-readable content. |
| DOC-REQ-009 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §6.1 UpdateInputs (lines 173-193) | `UpdateInputs` must support input/plan/parameter changes, return acceptance + apply mode, be idempotent, and reject invalid or unauthorized updates. |
| DOC-REQ-010 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §6.1 SetTitle/RequestRerun (lines 194-226) | `SetTitle` and `RequestRerun` updates must be supported, with rerun preferring Continue-As-New semantics. |
| DOC-REQ-011 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §6.2 (lines 233-261) | Signal contracts must support `ExternalEvent` and `Approve`, and optionally `Pause/Resume`, with authenticity verification delegated to Activities. |
| DOC-REQ-012 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §7 (lines 267-282) | User cancellation must transition to `canceled` with summary updates; forced termination must map to failed semantics with reason capture. |
| DOC-REQ-013 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §8 (lines 291-309) | Continue-As-New triggers and preservation rules must be implemented to control workflow history growth while preserving key identity and references. |
| DOC-REQ-014 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §9 (lines 316-339) | Workflow and activity timeout/retry defaults and callback-first monitoring with bounded polling/backoff must be enforced. |
| DOC-REQ-015 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §10 (lines 344-357) | Failure handling must classify errors into `user_error`, `integration_error`, `execution_error`, or `system_error`, and expose concise UI-facing summary details. |
| DOC-REQ-016 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §11 (lines 363-415) | `MoonMind.Run` and `MoonMind.ManifestIngest` lifecycles must follow the documented state transitions including `awaiting_external` and `finalizing` where applicable. |
| DOC-REQ-017 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §12 (lines 420-426) | Updates, signals, and cancels must enforce owner/admin authorization and support defense-in-depth verification paths. |
| DOC-REQ-018 | `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md` §13 + Appendix A (lines 433-470) | v1 implementation must satisfy fixed catalog/state/contracts and dashboard MVP list fields/actions needed for list/filter/control flows. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Track Executions by Workflow Type and Lifecycle (Priority: P1)

As a platform user, I can start and view executions as Temporal workflow rows with stable type and lifecycle state so list/filter/detail views are consistent and actionable.

**Why this priority**: This is the foundational behavior for all execution visibility and control.

**Independent Test**: Start one `MoonMind.Run` and one `MoonMind.ManifestIngest`, then validate lifecycle state transitions, list filtering by workflow type/state, and terminal state mapping without using any legacy taxonomy.

**Acceptance Scenarios**:

1. **Given** a newly created execution, **When** the workflow starts, **Then** it is visible as one dashboard row with `mm_state=initializing` and a valid `mm:<id>` Workflow ID.
2. **Given** a workflow reaches a Temporal close status, **When** the run completes, fails, or cancels, **Then** `mm_state` transitions to the matching terminal value (`succeeded`, `failed`, `canceled`).
3. **Given** list filtering is applied, **When** the user filters by state and workflow entry/type, **Then** results are returned entirely from Temporal Visibility fields and memo data.

---

### User Story 2 - Control Running Executions via Updates, Signals, and Cancel (Priority: P1)

As an owner or admin, I can edit execution inputs, send external/human events, cancel runs, and request reruns through defined contracts with authorization and invariant checks.

**Why this priority**: Lifecycle controls are core runtime operations and must be safe, predictable, and auditable.

**Independent Test**: Against a running workflow, call `UpdateInputs`, `SetTitle`, `RequestRerun`, `ExternalEvent`, and cancel; verify accepted/rejected behavior, auth handling, idempotency, and resulting states.

**Acceptance Scenarios**:

1. **Given** a running workflow and valid owner credentials, **When** `UpdateInputs` is requested, **Then** the workflow returns a structured acceptance response and applies the change using an allowed mode.
2. **Given** unauthorized credentials or terminal state, **When** an update/cancel/signal is requested, **Then** the request is rejected and the workflow invariants remain unchanged.
3. **Given** an approved rerun request, **When** rerun starts, **Then** execution proceeds via Continue-As-New under the same Workflow ID unless policy explicitly requires otherwise.

---

### User Story 3 - Ensure Runtime Robustness and History Safety (Priority: P2)

As a platform operator, I can rely on bounded history growth, defined timeout/retry behavior, and consistent error categorization for debugging and support.

**Why this priority**: Stability and operational clarity prevent degraded performance in long-running and integration-heavy workflows.

**Independent Test**: Drive long-running execution and external callback/polling paths, then verify Continue-As-New triggers, timeout/retry policies, and error-category outcomes.

**Acceptance Scenarios**:

1. **Given** an execution exceeds configured history or long-wait thresholds, **When** threshold is reached, **Then** Continue-As-New is triggered while preserving required identity and references.
2. **Given** external integration failures exceed retry budget, **When** workflow closes as failed, **Then** summary includes a supported error category and concise human-readable message.
3. **Given** monitoring depends on callback and polling fallback, **When** callback is absent, **Then** polling uses backoff and remains bounded by timeout policies.

### Edge Cases

- Update requests arrive concurrently with cancellation.
- External events arrive out of order, duplicated, or with invalid verification context.
- Workflow execution is terminated by operators during `awaiting_external`.
- Continue-As-New occurs near terminal completion and must not lose final summary/state.
- Very large input/manifest payloads are referenced by artifact pointer instead of workflow history/memo.
- `MoonMind.ManifestIngest` chooses inline orchestration for small manifests and child runs for large manifests while preserving lifecycle invariants.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST implement a v1 workflow type catalog with `MoonMind.Run` and `MoonMind.ManifestIngest` as the only root execution categories. (DOC-REQ-001, DOC-REQ-004, DOC-REQ-006)
- **FR-002**: The system MUST represent each dashboard list row as a single Temporal Workflow Execution sourced from Temporal Visibility only. (DOC-REQ-001, DOC-REQ-018)
- **FR-003**: The system MUST enforce workflow IDs formatted as `mm:<ulid-or-uuid>` and prevent sensitive data encoding in IDs surfaced by APIs. (DOC-REQ-005)
- **FR-004**: The system MUST define `mm_state` as a single domain state Search Attribute with allowed values `initializing|planning|executing|awaiting_external|finalizing|succeeded|failed|canceled`. (DOC-REQ-007)
- **FR-005**: The system MUST set `mm_state=initializing` at workflow start and transition to a terminal `mm_state` consistent with Temporal close status. (DOC-REQ-007)
- **FR-006**: The system MUST persist and expose required Visibility fields `mm_owner_id`, `mm_state`, and `mm_updated_at`, and support entry/type filtering needed for list operations. (DOC-REQ-008, DOC-REQ-018)
- **FR-007**: The system MUST persist and expose required memo metadata fields `title` and `summary` using bounded, human-readable content. (DOC-REQ-008, DOC-REQ-018)
- **FR-008**: The system MUST keep large payloads outside workflow history and memo by using artifact references in lifecycle and event contracts. (DOC-REQ-002)
- **FR-009**: The system MUST implement `UpdateInputs` update contract with structured response (`accepted`, `applied`, `message`) and idempotent semantics. (DOC-REQ-003, DOC-REQ-009)
- **FR-010**: The system MUST reject updates that violate invariants, including invalid artifacts, unauthorized access, or terminal-state restrictions. (DOC-REQ-009, DOC-REQ-017)
- **FR-011**: The system MUST implement `SetTitle` and `RequestRerun` updates, with rerun behavior preferring Continue-As-New under the same Workflow ID. (DOC-REQ-010)
- **FR-012**: The system MUST implement `ExternalEvent` and `Approve` signal contracts and process them asynchronously; `Pause/Resume` remains optional unless enabled by product policy. (DOC-REQ-003, DOC-REQ-011)
- **FR-013**: The system MUST verify external event authenticity through activity-driven validation paths before applying event effects. (DOC-REQ-011, DOC-REQ-017)
- **FR-014**: The system MUST support user cancel semantics that transition `mm_state` to `canceled`, attempt in-flight activity cancellation where possible, and produce a final summary. (DOC-REQ-012)
- **FR-015**: The system MUST support forced termination semantics that close in failed state with a captured reason in summary metadata. (DOC-REQ-012)
- **FR-016**: The system MUST implement Continue-As-New triggers for long execution history, prolonged waiting/polling, and major reconfiguration updates, while preserving workflow identity and required references. (DOC-REQ-013)
- **FR-017**: The system MUST enforce workflow/activity timeout and retry policies with callback-first external monitoring and bounded polling/backoff fallback behavior. (DOC-REQ-014)
- **FR-018**: The system MUST classify terminal failures into `user_error`, `integration_error`, `execution_error`, or `system_error`, and provide concise UI-facing summaries for failed runs. (DOC-REQ-015)
- **FR-019**: The system MUST implement lifecycle transitions for `MoonMind.Run` and `MoonMind.ManifestIngest` consistent with documented state diagrams and phase semantics. (DOC-REQ-016)
- **FR-020**: The system MUST enforce owner/admin authorization for update, signal, cancel, and rerun controls, with defense-in-depth checks at API and workflow boundaries. (DOC-REQ-017)
- **FR-021**: Deliverables for this feature MUST include production runtime code changes that implement these lifecycle/catalog behaviors and automated validation tests that verify contract and state invariants. (Runtime intent guard)

### Key Entities *(include if feature involves data)*

- **WorkflowTypeCatalogEntry**: Defines a supported workflow type, its user-facing label, and its lifecycle contract boundaries.
- **WorkflowExecutionRecord**: Represents one Temporal Workflow Execution row exposed to dashboard/API with Workflow ID, type, close status, and lifecycle state.
- **VisibilityMetadata**: Indexed fields used for filtering/sorting (`mm_owner_id`, `mm_state`, `mm_updated_at`, optional entry/type filters).
- **ExecutionMemo**: Human-readable, bounded metadata (`title`, `summary`, optional safe artifact references).
- **WorkflowUpdateRequest**: Owner/admin request to mutate running execution inputs, title, or rerun intent with idempotent result contract.
- **WorkflowSignalEvent**: Asynchronous external or human event (`ExternalEvent`, `Approve`, optional `Pause/Resume`) with verification context.
- **LifecyclePolicy**: Runtime policy set controlling Continue-As-New triggers, timeout budgets, retry defaults, and terminal mapping invariants.
- **ErrorOutcome**: UI-facing failure classification and summary derived from workflow orchestration boundaries.

### Assumptions & Dependencies

- UI may map workflow type names to friendly labels while preserving canonical internal type names.
- Detail pages default to latest run for a Workflow ID while retaining access to run history.
- `RequestRerun` defaults to Continue-As-New on the same Workflow ID in v1.
- `Pause/Resume` is optional for v1 and can be deferred unless explicitly enabled.
- `mm_updated_at` updates on state transitions and bounded progress updates.
- Existing artifact storage and authorization systems are available to support references and policy checks.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of newly started executions are queryable from Temporal Visibility with a valid `mm:<id>` Workflow ID, `mm_state`, `mm_owner_id`, and `mm_updated_at` present.
- **SC-002**: 100% of terminal executions map to consistent terminal `mm_state` values (`succeeded`, `failed`, `canceled`) matching Temporal close status in automated tests.
- **SC-003**: Update and signal contract tests demonstrate at least 95% first-attempt success for valid owner/admin requests and 100% rejection for unauthorized or invariant-violating requests.
- **SC-004**: Long-running lifecycle tests confirm Continue-As-New trigger behavior preserves Workflow ID and required metadata/references in 100% of triggered cases.
- **SC-005**: Failure-path tests show 100% of failed executions include one supported error category and concise summary metadata.
- **SC-006**: Release acceptance demonstrates deliverables include production runtime implementation changes plus automated validation tests, with no docs-only completion path.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode requirement coverage is explicit and deterministic in `tasks.md`:
  - Production runtime implementation task coverage: `T001-T008`, `T013-T017`, `T021-T026`, `T030-T034`.
  - Validation task coverage: `T009-T012`, `T018-T020`, `T027-T029`, `T035-T039`.
- `DOC-REQ-*` coverage guard is explicit:
  - Source requirements include `DOC-REQ-001` through `DOC-REQ-018`.
  - Deterministic implementation and validation task mappings are defined in the `DOC-REQ Coverage Matrix` in `tasks.md`, with source mapping maintained in `contracts/requirements-traceability.md`.

### MEDIUM/LOW remediation status

- Cross-artifact determinism is preserved by aligning runtime-mode scope and validation-gate language across `spec.md`, `plan.md`, and `tasks.md`.
- Runtime scope validation remains explicit through the runtime diff scope gate task (`T039`) and the plan-level remediation gate rules.

### Residual risks

- Multi-surface implementation (service/router/schema/model/migrations) can still drift if future edits bypass the task-level traceability matrix.
- Integration behavior for Temporal lifecycle transitions depends on maintaining parity between local and deployment Temporal environments.
