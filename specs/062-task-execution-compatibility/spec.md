# Feature Specification: Task Execution Compatibility

**Feature Branch**: `047-task-execution-compatibility`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs\Temporal\TaskExecutionCompatibilityModel.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §1, §3.1 (lines 12-23, 40-47) | The system MUST preserve a task-first product contract over Temporal-backed executions for list, detail, state, action, and pagination behavior during migration. |
| DOC-REQ-002 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §3.2, §6.1-6.4 (lines 49-55, 101-135) | The compatibility model MUST keep `queue`, `orchestrator`, and `temporal` as execution sources, MUST treat `proposals` and `schedules` as non-sources, and MUST model Temporal-backed manifest work as `source=temporal` plus `entry=manifest` without relabeling queue-backed manifest jobs. |
| DOC-REQ-003 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §5, §7.1-7.5 (lines 74-95, 141-186) | The product MUST keep `task` as the user-facing noun, `workflow execution` as the runtime noun, and MUST enforce opaque identifier handling where Temporal-backed `taskId == workflowId`, `temporalRunId` is detail-only, `runId` is not overloaded, and reruns keep stable task identity through Continue-As-New. |
| DOC-REQ-004 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §7.4, §13.2 (lines 167-176, 497-509) | Unified task detail routing MUST resolve through a canonical server-side source mapping/global task index rather than backend probing or ID-shape contracts. |
| DOC-REQ-005 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §8.1 (lines 194-223) | Temporal-backed task list rows MUST expose the normalized compatibility fields and safe defaults described by the document, including bounded, secret-safe Search Attributes and Memo data. |
| DOC-REQ-006 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §8.2 (lines 225-267) | Temporal-backed task detail payloads MUST include the documented required fields, optional action/debug blocks when available, and MUST expose only reviewed task-safe parameter data. |
| DOC-REQ-007 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §9.1-9.5 (lines 297-355) | The system MUST normalize Temporal-backed work into the dashboard status family while preserving raw `mm_state`, `temporalStatus`, and `closeStatus`, including special handling for `awaiting_external`, `TimedOut`, `Terminated`, and `ContinuedAsNew`. |
| DOC-REQ-008 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §10.1-10.4 (lines 363-401) | Task-facing create, edit, rename, rerun, approve, pause, resume, callback, and cancel actions MUST translate into the documented Temporal controls and respect accepted/applied result semantics, graceful cancel defaults, and authorization checks. |
| DOC-REQ-009 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §11.1-11.3 (lines 405-442) | Temporal-only and mixed-source task queries MUST follow the documented sorting, cursor, and count-mode rules, including not leaking raw Temporal cursors as mixed-source cursors and not claiming exact counts when exact aggregation is not available. |
| DOC-REQ-010 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §12.1-12.3 (lines 446-485) | During migration, `TemporalExecutionRecord` projections may power compatibility APIs, but Temporal lifecycle state/history and Temporal visibility metadata MUST be treated as the long-term source of truth, with canonical metadata fields and bounded secret-safe Memo/Search Attributes. |
| DOC-REQ-011 | `docs/Temporal/TaskExecutionCompatibilityModel.md` §13.1-13.3 (lines 489-520) | The dashboard MUST add `temporal` as an execution source, keep `/tasks/list` and `/tasks/{taskId}` as canonical routes, render Temporal-backed details inside the unified task shell, and support manifest task views through `source=temporal` plus `entry=manifest` without requiring a new source taxonomy. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Temporal Work as Tasks (Priority: P1)

As an operator using the existing task dashboard, I can list and inspect Temporal-backed executions through the same task-oriented surfaces so migration does not split the product into incompatible execution models.

**Why this priority**: This is the core compatibility promise in the source document; without it, Temporal adoption breaks the existing task UX and API contract.

**Independent Test**: Run Temporal-backed executions and verify `/tasks/list` and `/tasks/{taskId}` return normalized task-compatible rows/details with the documented source, entry, identifier, and metadata fields.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed `MoonMind.Run`, **When** it appears in the task list, **Then** it is rendered as `source=temporal`, `entry=run`, and `taskId == workflowId` with normalized title, summary, state, and timestamps.
2. **Given** a Temporal-backed `MoonMind.ManifestIngest`, **When** it appears in task views, **Then** it remains `source=temporal` with `entry=manifest` rather than introducing a new `manifest` execution source.
3. **Given** an operator opens `/tasks/{taskId}` for a Temporal-backed execution, **When** detail data is returned, **Then** the response includes the documented required detail fields and keeps debug/raw lifecycle data available without exposing unsafe free-form parameters.

---

### User Story 2 - Operate Temporal Executions Through Task Controls (Priority: P1)

As a user of task-oriented controls, I can edit, approve, pause, resume, rerun, cancel, and deliver callbacks to Temporal-backed work through task semantics so product behavior remains stable while the runtime changes underneath.

**Why this priority**: Compatibility fails if actions still require Temporal-specific concepts in the UI or if reruns and cancels break task identity.

**Independent Test**: Exercise task-facing actions against Temporal-backed executions and verify action routing, result semantics, graceful cancellation, and stable task identity across reruns.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed task, **When** a user edits inputs or requests a rerun, **Then** the compatibility layer returns accepted/applied/message result semantics and keeps the stable `taskId` anchored to the durable `workflowId`.
2. **Given** approval, pause, resume, or callback delivery is requested, **When** the compatibility layer handles the action, **Then** it routes to the documented Temporal control while preserving task-facing product language and authorization checks.
3. **Given** a running Temporal-backed task is cancelled, **When** standard task cancellation is invoked, **Then** the system performs graceful cancel semantics by default and exposes forced termination only as an explicit operator/admin path.

---

### User Story 3 - Query Mixed Sources Without Losing Meaning (Priority: P2)

As a dashboard user or API consumer, I can query Temporal-only and mixed-source task views with consistent sorting, counts, and status meaning so task lists stay reliable during migration.

**Why this priority**: Mixed-source list behavior is where migration regressions become visible first; count, cursor, and status drift would immediately break operator trust.

**Independent Test**: Run Temporal-only and mixed-source list queries and confirm normalized statuses, preserved raw state fields, merged pagination behavior, and correct count mode signaling.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed task with `mm_state=awaiting_external`, **When** it is shown in the task dashboard, **Then** the normalized status is `awaiting_action` while detail data still distinguishes approval, pause, callback wait, or integration wait context.
2. **Given** a mixed-source task list, **When** pagination is requested, **Then** the response uses compatibility-owned merged cursor behavior and does not expose a raw Temporal page token as the universal cursor.
3. **Given** exact aggregated counts are not cheaply available, **When** count data is returned, **Then** the response marks the count as `estimated_or_unknown` or omits it rather than falsely claiming exactness.

### Edge Cases

- A client attempts to resolve `/tasks/{taskId}` using only ID shape after rerun/Continue-As-New changes the current `temporalRunId`.
- Queue-backed manifest jobs and Temporal-backed manifest executions coexist in the same deployment during migration.
- A Temporal execution closes as `TimedOut`, `Terminated`, or `ContinuedAsNew` and the dashboard must normalize status without hiding raw close semantics.
- A detail response includes Search Attributes, Memo, and parameters that may contain oversized or secret material.
- Mixed-source task queries combine sources with different pagination contracts and exact-count availability.
- Users invoke task-facing actions against already terminal executions and expect explicit no-op or unavailable messaging.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement the task-to-Temporal compatibility behavior described by this feature and MUST NOT be satisfied by docs-only updates. (Maps: DOC-REQ-001)
- **FR-002**: The delivery MUST include automated validation tests covering normalized task list/detail payloads, routing, status mapping, action mapping, and pagination/count behavior for Temporal-backed compatibility flows. (Maps: DOC-REQ-001)
- **FR-003**: The system MUST preserve the execution source set `queue`, `orchestrator`, and `temporal` for task list/detail behavior and MUST NOT reclassify `proposals` or `schedules` as execution sources. (Maps: DOC-REQ-002)
- **FR-004**: The system MUST represent Temporal-backed `MoonMind.Run` executions as `source=temporal` plus `entry=run` and Temporal-backed `MoonMind.ManifestIngest` executions as `source=temporal` plus `entry=manifest`. (Maps: DOC-REQ-002)
- **FR-005**: The system MUST keep queue-backed manifest jobs labeled as `source=queue` until their runtime actually migrates and MUST NOT relabel them as Temporal-backed prematurely. (Maps: DOC-REQ-002)
- **FR-006**: The system MUST keep `task` as the user-facing noun for compatibility surfaces while using `workflow execution` as the runtime term in Temporal-facing layers. (Maps: DOC-REQ-003)
- **FR-007**: For Temporal-backed task rows and details, `taskId` MUST equal the durable `workflowId`, `temporalRunId` MUST remain detail/debug-only, and legacy `runId` MUST NOT be repurposed to mean Temporal run identity. (Maps: DOC-REQ-003)
- **FR-008**: Clients and adapters MUST treat task and workflow identifiers as opaque strings and MUST NOT rely on textual ID shape as the compatibility contract for backend selection. (Maps: DOC-REQ-003, DOC-REQ-004)
- **FR-009**: The compatibility layer MUST resolve `/tasks/{taskId}` through a canonical server-side source mapping or global task index instead of probing queue, orchestrator, and Temporal backends heuristically. (Maps: DOC-REQ-004)
- **FR-010**: Reruns implemented through Continue-As-New MUST keep the stable compatibility `taskId` and `workflowId` even when the current `temporalRunId` changes. (Maps: DOC-REQ-003, DOC-REQ-004)
- **FR-011**: Temporal-backed task list payloads MUST expose the normalized fields defined by the source document, including `taskId`, `source`, `entry`, `title`, `summary`, `status`, `rawState`, `temporalStatus`, `workflowId`, `workflowType`, owner metadata, lifecycle timestamps, artifact count, and canonical detail route. (Maps: DOC-REQ-005)
- **FR-012**: Temporal-backed task list payloads MUST provide safe defaults for missing titles, use `startedAt` as `createdAt`, and keep Search Attributes and Memo data bounded and secret-safe. (Maps: DOC-REQ-005)
- **FR-013**: Temporal-backed task detail payloads MUST include the documented required identity, lifecycle, owner, artifact, Search Attribute, and Memo fields and MAY expose action/debug blocks only when they are task-safe and reviewed. (Maps: DOC-REQ-006)
- **FR-014**: Task detail responses MUST NOT blindly surface raw execution parameters and MUST expose only reviewed task-safe parameter fields. (Maps: DOC-REQ-006)
- **FR-015**: The dashboard-normalized task status family for Temporal-backed work MUST remain `queued`, `running`, `awaiting_action`, `succeeded`, `failed`, and `cancelled`. (Maps: DOC-REQ-007)
- **FR-016**: The compatibility layer MUST map `initializing` to `queued`, `planning|executing|finalizing` to `running`, `awaiting_external` to `awaiting_action`, `succeeded` to `succeeded`, `failed` to `failed`, and `canceled` to `cancelled`. (Maps: DOC-REQ-007)
- **FR-017**: Temporal-backed payloads MUST preserve raw `rawState`, `temporalStatus`, and `closeStatus` data even when a normalized dashboard `status` is also returned. (Maps: DOC-REQ-007)
- **FR-018**: `TimedOut` and `Terminated` close outcomes MUST normalize to dashboard `failed`, and `ContinuedAsNew` MUST remain run-history/debug information rather than a user-facing terminal status for the stable task. (Maps: DOC-REQ-007)
- **FR-019**: Task-facing create, edit, rename, rerun, approve, pause, resume, external callback, and cancel operations MUST translate into the documented Temporal start, update, signal, or cancel controls through compatibility adapters. (Maps: DOC-REQ-008)
- **FR-020**: Temporal-backed edit and rerun actions MUST surface compatibility outcomes using accepted/applied/message semantics and MUST allow rerun flows that keep the same `taskId` while changing `temporalRunId`. (Maps: DOC-REQ-008)
- **FR-021**: Approval and callback behavior MUST remain expressed in product-facing task semantics, and compatibility layers MUST validate ownership and authorization before routing those actions to Temporal controls. (Maps: DOC-REQ-008)
- **FR-022**: Standard task cancellation MUST default to graceful workflow cancellation, while forced termination MUST remain a separate operator/admin path and terminal executions MUST render cancellation as unavailable or explicit no-op messaging. (Maps: DOC-REQ-008)
- **FR-023**: Temporal-only task queries MUST default to `updatedAt DESC`, MAY retain Temporal-specific page tokens for Temporal-only views, and MAY report exact counts only when cheaply available. (Maps: DOC-REQ-009)
- **FR-024**: Mixed-source task queries MUST use a compatibility-owned merged pagination contract, MUST NOT leak a raw Temporal page token as the universal cursor, and MUST keep global sorting consistent across sources. (Maps: DOC-REQ-009)
- **FR-025**: Where counts are returned, responses MUST expose `countMode` as either `exact` or `estimated_or_unknown`, and unified `/tasks/*` views MUST NOT claim exact counts unless they truly have them. (Maps: DOC-REQ-009)
- **FR-026**: Near-term compatibility implementations MAY use `TemporalExecutionRecord` projections and lifecycle service facades, but they MUST align with the documented execution contract rather than inventing source-specific shadow semantics. (Maps: DOC-REQ-010)
- **FR-027**: For Temporal-managed work, execution lifecycle truth MUST come from Temporal workflow state/history and list/filter/query truth MUST come from Temporal visibility metadata, with MoonMind projections limited to read-model or cache roles. (Maps: DOC-REQ-010)
- **FR-028**: Canonical metadata for Temporal-backed compatibility MUST include Search Attributes `mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`, optional bounded indexed metadata, and Memo fields `title`, `summary`, and optional safe references. (Maps: DOC-REQ-010)
- **FR-029**: Search Attributes and Memo used for compatibility MUST remain bounded and secret-safe and MUST NOT become dump targets for raw prompts, credentials, or other unreviewed large payloads. (Maps: DOC-REQ-005, DOC-REQ-006, DOC-REQ-010)
- **FR-030**: The dashboard runtime source model MUST add `temporal` as an execution source for task list/detail behavior without requiring proposal or schedule pages to join that taxonomy. (Maps: DOC-REQ-011)
- **FR-031**: Preferred compatibility routes MUST remain `/tasks/list` and `/tasks/{taskId}`, and Temporal-backed details MUST render within the same unified task shell used for other task sources. (Maps: DOC-REQ-011)
- **FR-032**: If source-specific convenience routes exist for Temporal-backed tasks, they MUST remain optional sugar and MUST preserve the canonical `taskId == workflowId` route contract. (Maps: DOC-REQ-004, DOC-REQ-011)

### Key Entities *(include if feature involves data)*

- **Task Compatibility Record**: Normalized task-facing representation of an execution used by task list/detail surfaces regardless of whether the underlying substrate is queue, orchestrator, or Temporal.
- **Temporal Task Identity**: Durable identifier set for Temporal-backed compatibility rows consisting of `taskId`, `workflowId`, and detail/debug `temporalRunId`.
- **Temporal Task Row**: Normalized list payload containing user-facing title/summary, normalized and raw states, owner metadata, timestamps, and canonical detail route.
- **Temporal Task Detail**: Expanded compatibility payload for one Temporal-backed task including artifact references, Search Attributes, Memo fields, optional actions, and optional debug context.
- **Task Source Mapping**: Canonical server-side index used to resolve `/tasks/{taskId}` to the correct execution source and durable identity.
- **Task Action Result**: Compatibility response for update/signal/cancel-style operations that communicates whether the request was accepted, how it was applied, and any operator-facing message.
- **Task Count Contract**: Response metadata that communicates whether returned counts are exact or estimated/unknown for a task list query.

### Assumptions & Dependencies

- Existing `/api/executions` lifecycle APIs remain available as the Temporal-backed control surface during migration.
- `MoonMind.Run` and `MoonMind.ManifestIngest` are the Temporal workflow types participating in this compatibility model for v1.
- The dashboard and API already have task list/detail surfaces that can consume normalized compatibility payloads.
- Authorization and ownership checks required for task-facing actions are available from existing execution and principal metadata.
- Mixed-source task queries may need compatibility-layer aggregation logic because underlying sources do not share one native cursor/count contract.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of Temporal-backed task list responses returned by validation tests include the documented normalized identity, source, entry, status, owner, and timestamp fields, with `taskId == workflowId`.
- **SC-002**: 100% of Temporal-backed detail responses returned by validation tests preserve raw `rawState`, `temporalStatus`, and `closeStatus` data alongside the normalized dashboard `status`.
- **SC-003**: Rerun validation shows the stable task detail route remains usable across Continue-As-New in 100% of tested cases, with `workflowId` unchanged and `temporalRunId` allowed to change.
- **SC-004**: Mixed-source pagination validation confirms 100% of tested unified task queries use compatibility-owned cursor/count behavior and never expose a raw Temporal page token as the universal cursor.
- **SC-005**: Action-mapping validation confirms 100% of tested task-facing actions route to the documented Temporal controls and return accepted/applied/message outcomes or explicit unavailability messaging that matches the compatibility contract.
- **SC-006**: Release acceptance for this feature verifies that deliverables include production runtime implementation changes plus automated validation tests, with no docs-only completion path.

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode implementation and validation coverage is explicit and deterministic in `tasks.md`:
  - Production runtime implementation task coverage: `T001-T008`, `T013-T017`, `T021-T026`, `T030-T034`.
  - Validation task coverage: `T009-T012`, `T018-T020`, `T027-T029`, `T036-T038`.
- `DOC-REQ-*` traceability coverage is explicit and auditable:
  - Source requirements include `DOC-REQ-001` through `DOC-REQ-011`.
  - Deterministic implementation-task and validation-task mappings are defined in the `DOC-REQ Coverage Matrix` in `tasks.md` and mirrored in `contracts/requirements-traceability.md`.
- Validation execution is explicit and non-ambiguous across the planning artifacts:
  - `./tools/test_unit.sh` is the required unit/dashboard validation entrypoint.
  - Targeted contract pytest coverage for `tests/contract/test_task_compatibility_api.py` and `tests/contract/test_temporal_execution_api.py` is called out separately in `plan.md`, `tasks.md`, and `quickstart.md`.

### MEDIUM/LOW remediation status

- Cross-artifact determinism is preserved by aligning runtime-mode scope, `DOC-REQ-*` coverage, and validation-gate language across `spec.md`, `plan.md`, `tasks.md`, and `contracts/requirements-traceability.md`.
- Prompt B scope controls remain explicit in `tasks.md`, so runtime implementation and validation expectations stay visible if the task list is regenerated.

### Residual risks

- Multi-surface implementation across routers, schemas, services, dashboard code, and migrations can still drift if future edits bypass the shared `DOC-REQ` coverage matrix and traceability table.
- Mixed-source pagination and source-resolution behavior remain sensitive to legacy rows that lack mapping entries until runtime backfill logic is implemented and verified.
