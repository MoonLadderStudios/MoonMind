# Feature Specification: Run History and Rerun Semantics

**Feature Branch**: `048-run-history-rerun`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/Temporal/RunHistoryAndRerunSemantics.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."  
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §4, §5.1 (lines 49-75) | Temporal-backed execution state is a logical execution keyed by `workflowId`, with one current/latest materialized view for that logical execution. |
| DOC-REQ-002 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §5.1 (lines 66-75) | `workflowId` is the canonical durable identifier for Temporal-backed work and must remain the preferred route, bookmark, and compatibility anchor across Continue-As-New. |
| DOC-REQ-003 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §5.2, §5.4 (lines 76-108) | `runId` identifies only one Temporal run instance, is valid for debugging/correlation, is not the primary product handle, and should be disambiguated as `temporalRunId` when external payloads need explicit naming. |
| DOC-REQ-004 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §5.3 (lines 86-95) | Task-oriented compatibility surfaces must resolve to the logical execution and must use `taskId == workflowId` for Temporal-backed rows. |
| DOC-REQ-005 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §6.1-6.4 (lines 112-155) | v1 run history semantics use one detail page per `workflowId` that always shows the latest/current run, while per-run history lists, arbitrary historical-run routes, and immutable per-run snapshots remain out of scope. |
| DOC-REQ-006 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §6.2, §10.1-10.3 (lines 129-155, 266-301) | Detail and list rendering must keep route identity anchored on `workflowId`/`taskId`, show latest-run metadata, resolve artifacts using the latest `runId`, and keep list-row identity stable across Continue-As-New. |
| DOC-REQ-007 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §7.1-7.2 (lines 159-180) | `RequestRerun` means a clean rerun of the same logical execution via Continue-As-New that preserves `workflowId`, rotates `runId`, resets run-local state, and records rerun summary metadata. |
| DOC-REQ-008 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §7.3 (lines 182-194) | Rerun may replace or patch execution inputs (`input_ref`, `plan_ref`, `parameters_patch`), remains the same logical execution, stores audit-relevant inputs as artifacts/references, and must honor idempotency keys. |
| DOC-REQ-009 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §7.4 (lines 196-205) | Accepted rerun transitions `MoonMind.Run` back to `planning` or `executing` depending on `plan_ref`, and transitions `MoonMind.ManifestIngest` back to `executing`. |
| DOC-REQ-010 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §7.5 (lines 207-220) | Terminal executions currently do not accept rerun as an implicit exception; rerun-from-terminal requires an explicit future change or dedicated restart surface. |
| DOC-REQ-011 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §8 (lines 222-245) | Automatic Continue-As-New for lifecycle rollover or major reconfiguration preserves the same logical execution but must not be labeled as a manual rerun, and `rerun_count` alone does not prove user intent. |
| DOC-REQ-012 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §9 (lines 247-262) | New logical work must start a fresh `workflowId`, while rerunning the same logical work must reuse the same `workflowId` through Continue-As-New. |
| DOC-REQ-013 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §10 (lines 264-313) | Execution APIs and compatibility routes must return the latest/current execution view keyed by `workflowId`, report accepted reruns as `continue_as_new`, and treat changed `runId` as normal after rerun or rollover. |
| DOC-REQ-014 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §11 (lines 314-322) | The application database remains a latest-run projection keyed by `workflowId`; immutable per-run evidence must come from artifacts/summaries or a future dedicated `workflowId + runId` read model. |
| DOC-REQ-015 | `docs/Temporal/RunHistoryAndRerunSemantics.md` §12-13 (lines 324-350) | Acceptance for this feature requires aligned runtime behavior, compatibility semantics, adjacent API/UI contracts, and validation proving rerun preserves `workflowId` while rotating `runId`. |
| DOC-REQ-016 | Task objective runtime scope guard | Delivery must include production runtime code changes implementing run-history and rerun semantics plus automated validation tests; docs-only completion is not acceptable. |

Each `DOC-REQ-*` listed above maps to at least one functional requirement below.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Keep One Stable Detail Route Across Reruns (Priority: P1)

As a dashboard user, I can keep following the same task or execution detail after rerun or lifecycle rollover without having to understand Temporal run-instance changes.

**Why this priority**: Stable identity is the core product promise this feature defines for Temporal-backed work.

**Independent Test**: Start a Temporal-backed execution, capture its `workflowId`/`taskId`, trigger rerun or other Continue-As-New behavior, and verify the same detail route now shows the latest run metadata and artifacts.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed task detail route keyed by `taskId`, **When** the backing execution continues as new, **Then** the route still resolves to the same logical execution and displays the latest/current run.
2. **Given** the execution list shows a row for a Temporal-backed workflow, **When** rerun rotates the current `runId`, **Then** the same row remains visible with refreshed state, summary, and latest run metadata rather than a sibling row.
3. **Given** the detail page needs artifacts after rerun, **When** artifact data is requested, **Then** the fetch uses the latest `runId` resolved from execution detail while the user-facing route remains anchored on `workflowId`.

---

### User Story 2 - Rerun the Same Logical Execution Predictably (Priority: P1)

As a workflow author or API client, I can request rerun on an active Temporal execution and get consistent lifecycle behavior, input handling, and terminal-state rules.

**Why this priority**: Rerun is the explicit control surface this document is standardizing, and downstream automation depends on deterministic behavior.

**Independent Test**: Issue `RequestRerun` updates against active and terminal executions, including cases with input or plan replacements and idempotency keys, and verify the response contract and resulting execution state.

**Acceptance Scenarios**:

1. **Given** an active `MoonMind.Run` execution, **When** `RequestRerun` is accepted, **Then** the execution preserves `workflowId`, rotates `runId`, resets run-local counters, and re-enters `planning` or `executing` based on whether `plan_ref` is present.
2. **Given** a rerun request includes `input_ref`, `plan_ref`, or `parameters_patch`, **When** the rerun is applied, **Then** those changes remain part of the same logical execution and audit-relevant references are retained through artifacts or references.
3. **Given** a terminal execution, **When** `RequestRerun` is submitted, **Then** the runtime does not silently restart the execution and instead returns the documented non-applied posture until a dedicated terminal-rerun path exists.

---

### User Story 3 - Distinguish Manual Rerun from Other Lifecycle Rollover (Priority: P2)

As an operator, I can tell whether the system preserved the same logical execution because of a user rerun or because lifecycle policy triggered Continue-As-New for history control or major reconfiguration.

**Why this priority**: Operator trust depends on not conflating automatic rollover with user intent or new-task creation.

**Independent Test**: Exercise threshold-driven Continue-As-New, explicit rerun, and a brand-new execution start, then verify identifiers, labels, and route semantics remain correct for each case.

**Acceptance Scenarios**:

1. **Given** lifecycle thresholds trigger Continue-As-New without a user rerun action, **When** the execution is observed in list or detail views, **Then** it remains the same logical execution and is not mislabeled as a user-visible rerun solely because counters changed.
2. **Given** the user intends to create a new logical task rather than re-execute the same one, **When** the start request is issued, **Then** the system creates a fresh `workflowId` instead of reusing `RequestRerun`.
3. **Given** a support or operator surface shows run-instance metadata, **When** it needs to identify a specific Temporal run, **Then** it uses run-instance naming that stays distinct from the logical execution handle and legacy orchestrator-era `runId` meanings.

### Edge Cases

- Continue-As-New occurs between a list snapshot and detail-page render, so the detail flow must resolve the latest `runId` without breaking the route anchored on `workflowId`.
- A terminal execution receives `RequestRerun` with an idempotency key and must return a stable non-applied result rather than silently creating a restart.
- A materially new plan replacement triggers Continue-As-New but must not be presented to users as though they clicked rerun.
- Compatibility payloads need to carry both durable task identity and current run-instance identity without reusing ambiguous legacy `runId` naming.
- Operators need historical run debugging for support, but the main product surface must not imply a first-class v1 run-history browser that does not exist.
- `startedAt` remains the logical execution start after rerun, so consumers must not infer "latest run started at" from that field alone.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement these run-history and rerun semantics, plus automated validation tests; docs-only completion is not acceptable. (DOC-REQ-016)
- **FR-002**: The system MUST treat `workflowId` as the canonical durable identity for Temporal-backed execution detail, task detail, bookmarks, compatibility routes, and rerun continuity. (DOC-REQ-001, DOC-REQ-002)
- **FR-003**: The system MUST treat `runId` as current-run diagnostic metadata rather than the primary product route key, and any external payload that needs explicit disambiguation MUST use terminology equivalent to `temporalRunId` rather than overloading legacy `runId` semantics. (DOC-REQ-003)
- **FR-004**: Temporal-backed compatibility surfaces MUST resolve task-oriented detail routes to the logical execution and MUST enforce `taskId == workflowId` for Temporal-backed rows and payloads. (DOC-REQ-004, DOC-REQ-015)
- **FR-005**: v1 detail surfaces MUST provide one logical detail page per `workflowId` that always resolves to the latest/current run and MUST NOT require users to choose a historical run in the main dashboard flow. (DOC-REQ-005, DOC-REQ-015)
- **FR-006**: List surfaces MUST preserve one stable row identity across manual rerun and automatic Continue-As-New, updating run metadata on the same logical row instead of creating sibling rows for the same logical execution. (DOC-REQ-005, DOC-REQ-006)
- **FR-007**: Detail and artifact flows MUST resolve artifacts using the latest `runId` from execution detail while keeping the route anchored on `workflowId`, and current `startedAt` behavior MUST remain the logical execution start unless a separate current-run start field is explicitly introduced later. (DOC-REQ-006)
- **FR-008**: v1 runtime behavior MUST keep run history as a conceptual or Temporal-native concern rather than exposing a first-class MoonMind per-run history list, arbitrary historical-run route, or immutable per-run projection in the application database. (DOC-REQ-005, DOC-REQ-014)
- **FR-009**: `RequestRerun` MUST execute as Continue-As-New for the same logical execution by preserving `workflowId`, rotating `runId`, incrementing `rerun_count`, clearing terminal and transient waiting/paused markers, resetting run-local lifecycle counters, retaining required logical metadata, and updating summary/memo to record rerun. (DOC-REQ-007)
- **FR-010**: `RequestRerun` MUST accept rerun-time replacements or patches for `input_ref`, `plan_ref`, and `parameters_patch` while preserving logical execution identity, storing audit-relevant inputs as artifacts or artifact references, and honoring caller idempotency keys. (DOC-REQ-008)
- **FR-011**: After an accepted rerun, `MoonMind.Run` MUST restart in `planning` when no `plan_ref` is present and in `executing` when `plan_ref` already exists, while `MoonMind.ManifestIngest` MUST restart in `executing`. (DOC-REQ-009)
- **FR-012**: Terminal executions MUST continue rejecting ordinary updates by default, and rerun-from-terminal MUST NOT be implied, silently performed, or treated as supported behavior until an explicit exception or dedicated restart surface is implemented. (DOC-REQ-010)
- **FR-013**: Automatic Continue-As-New caused by lifecycle thresholds or major reconfiguration MUST preserve stable logical execution identity and routing but MUST NOT be labeled as a user-visible rerun solely because `rerun_count` increased. (DOC-REQ-011)
- **FR-014**: The runtime MUST Continue-As-New for the documented same-execution lifecycle cases, including history-threshold rollover, step-count or wait-cycle thresholds, materially new plan replacement, and explicit `request_continue_as_new` input semantics when they still represent the same logical execution. (DOC-REQ-011)
- **FR-015**: When user or system intent is to create a new logical execution rather than rerun the same one, the system MUST start a fresh `workflowId` instead of reusing `RequestRerun` on the existing execution. (DOC-REQ-012)
- **FR-016**: Execution detail APIs keyed by `workflowId` MUST return the latest/current materialized execution view, and accepted `RequestRerun` responses MUST report `applied="continue_as_new"` while callers treat changed `runId` as a normal outcome of rerun or lifecycle rollover. (DOC-REQ-013)
- **FR-017**: Projection and audit behavior MUST treat the application database as a latest-run projection only, with immutable reconfiguration or input evidence preserved through artifacts and summaries rather than inferred from one mutable projection row. (DOC-REQ-014)
- **FR-018**: Validation coverage MUST prove `workflowId` stability, `taskId == workflowId` compatibility, `runId` rotation, latest-run detail and artifact resolution, terminal-state rerun rejection, and the distinction between manual rerun and automatic Continue-As-New. (DOC-REQ-015)
- **FR-019**: Any downstream dashboard or compatibility changes required by this feature MUST keep `workflowId` canonical and latest-run semantics explicit across execution detail, task detail, and artifact-fetch surfaces. (DOC-REQ-006, DOC-REQ-013, DOC-REQ-015)

### Key Entities *(include if feature involves data)*

- **LogicalExecutionIdentity**: The durable identity for one Temporal-backed work item, anchored on `workflowId` and reused across Continue-As-New transitions.
- **TemporalRunInstance**: One concrete Temporal run identified by `runId`, used for debugging, support, operator metadata, and latest-run artifact resolution.
- **ExecutionProjectionRecord**: The current application-side projection row keyed by `workflowId` that holds latest-run state, summary, counters, timestamps, and references.
- **TaskCompatibilityIdentifier**: The task-facing identity bridge that maps Temporal-backed `taskId` values directly to `workflowId`.
- **RerunRequest**: The update request that can carry rerun intent, replacement references, parameters patches, and idempotency semantics for the same logical execution.
- **ContinueAsNewCause**: The classified reason why the current run rolled forward, distinguishing manual rerun from lifecycle-threshold rollover or major reconfiguration.
- **ExecutionDetailView**: The latest-run materialized detail response that combines logical execution identity with current run metadata, state, and artifact references.

### Assumptions & Dependencies

- Existing Temporal execution APIs, execution projection storage, and dashboard runtime configuration remain the primary surfaces this feature will align rather than replace.
- Artifact retrieval may still require explicit namespace and current run-instance inputs internally even when the user-facing route remains anchored on `workflowId`.
- A richer per-run history endpoint, drawer, or operator-only inspection surface is future work and not required for v1 completion.
- If `rerunCount` is surfaced later, product copy must explain that it is currently a broad Continue-As-New counter rather than a pure manual-rerun counter.
- Related documentation updates may be needed for consistency, but they cannot be the only deliverable because this feature is runtime intent only.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated rerun tests show 100% of accepted reruns preserve `workflowId` and rotate `runId` while returning the expected latest/current execution view.
- **SC-002**: Automated compatibility tests show 100% of Temporal-backed task detail routes continue resolving through `taskId == workflowId` across rerun or other Continue-As-New transitions.
- **SC-003**: Automated negative-path tests show 100% of terminal-state `RequestRerun` attempts receive the documented non-applied posture until an explicit terminal-rerun path exists.
- **SC-004**: Automated lifecycle tests show threshold-driven Continue-As-New and manual rerun both preserve the same logical row identity while remaining distinguishable in product/operator semantics.
- **SC-005**: Automated detail and artifact tests show the latest/current run metadata is used for artifact resolution after rerun without requiring users to navigate to historical run-specific routes.
- **SC-006**: Release acceptance demonstrates production runtime implementation changes plus automated validation tests for these semantics, with no docs-only completion path accepted as done.
