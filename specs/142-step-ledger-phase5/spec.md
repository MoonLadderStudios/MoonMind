# Feature Specification: Step Ledger Phase 5

**Feature Branch**: `142-step-ledger-phase5`  
**Created**: 2026-04-08  
**Status**: Draft  
**Input**: User description: "Implement Phase 5 using test-driven development of the step-ledger rollout plan. Reconcile review/checks into the workflow-owned step ledger so approval-policy review state, verdicts, retry counts, and review evidence appear inside the Steps UI without parsing logs."

## Source Document Requirements

Source: `docs/Temporal/StepLedgerAndProgressModel.md`, `docs/Tasks/StepReviewGateSystem.md`, `docs/UI/MissionControlArchitecture.md`, `docs/UI/MissionControlStyleGuide.md`, `docs/tmp/remaining-work/Temporal-WorkflowTypeCatalogAndLifecycle.md`

| ID | Source Section | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `StepLedgerAndProgressModel` §8-§10 | `MoonMind.Run` step rows must use `reviewing` as a first-class status during structured review/check work and preserve run-scoped attempt semantics. |
| DOC-REQ-002 | `StepLedgerAndProgressModel` §9 | `checks[]` is the canonical structured location for review/check verdicts, retry counts, summaries, and bounded artifact refs. |
| DOC-REQ-003 | `StepLedgerAndProgressModel` §3.3, §9 | Large review payloads and feedback must remain out of workflow state and be linked through artifact refs instead. |
| DOC-REQ-004 | `StepReviewGateSystem` §1, §5.1, §5.2 | Approval policy should wrap eligible step execution with review, retry failed reviews with feedback, and treat `INCONCLUSIVE` as accepted execution. |
| DOC-REQ-005 | `StepReviewGateSystem` §2, §8 | Mission Control must expose review verdicts, retry progress, and review state as operator-visible step evidence rather than log conventions. |
| DOC-REQ-006 | `MissionControlArchitecture` §9.2 | Task detail pages are the correct surface for step evidence, checks, managed-run observability, and review results. |
| DOC-REQ-007 | `MissionControlStyleGuide` §5 | Review/check badges and summaries must remain compact, readable, and consistent with existing Mission Control semantic classes. |
| DOC-REQ-008 | `remaining-work/Temporal-WorkflowTypeCatalogAndLifecycle` | Workflow lifecycle coverage must include `step reviewing` transitions and remain replay-safe at the workflow boundary. |

## User Scenarios & Testing

### User Story 1 - Approval policy becomes the first real `checks[]` producer (Priority: P1)

An operator enables approval-policy review for a plan and expects the step ledger to show live review state and final verdicts directly on the step row.

**Why this priority**: Phase 5 exists to prevent the review system from creating a parallel state machine outside the step ledger.

**Independent Test**: Execute the workflow boundary with an approval-policy-enabled plan and assert the step row transitions through `reviewing`, emits a structured `approval_policy` check, and ends with a bounded final verdict plus retry count.

**Acceptance Scenarios**:

1. **Given** an eligible step completes execution and approval policy is enabled, **When** the review activity starts, **Then** the step row enters `reviewing` and `checks[]` contains a pending `approval_policy` row.
2. **Given** the review verdict is `PASS`, **When** review finishes, **Then** the step completes successfully and the final `approval_policy` check shows a passed verdict, bounded summary, retry count, and review evidence artifact ref.
3. **Given** the review verdict is `INCONCLUSIVE`, **When** review finishes, **Then** the workflow accepts the step execution without introducing a second hidden state machine, while still surfacing the review outcome in `checks[]`.

---

### User Story 2 - Failed reviews retry with feedback while staying inside one step row (Priority: P1)

An operator needs approval-policy failures to retry the same logical step and expose retry counts and latest review evidence without reading raw logs.

**Why this priority**: The main product risk is splitting retry/review state across workflow summaries, logs, and the UI. The step row must remain authoritative.

**Independent Test**: Drive a FAIL then PASS sequence through the workflow boundary and assert the same logical step row increments attempt, records retry count, stores only bounded summary state, and writes full review payloads to artifacts.

**Acceptance Scenarios**:

1. **Given** the first review verdict is `FAIL` and retries remain, **When** the workflow prepares the retry, **Then** the step row records the failed review in `checks[]`, keeps review details bounded, and reruns the same logical step with injected feedback.
2. **Given** a later review verdict passes after one or more failed reviews, **When** the step completes, **Then** the final step row shows the terminal verdict, accumulated retry count, and latest review evidence artifact ref.
3. **Given** review feedback contains long text or issues, **When** the workflow stores review evidence, **Then** the step row stores only a bounded summary and artifact ref, not the full review body.

---

### User Story 3 - Mission Control exposes review verdicts inside the expanded step panel (Priority: P2)

An operator opens the Steps panel and expects review status, retry counts, and linked review evidence to be visible in the existing Checks section without leaving the step row.

**Why this priority**: Phase 4 already made Steps the primary detail surface; Phase 5 must finish that surface for review/check work.

**Independent Test**: Render a step row with approval-policy check data and assert the expanded Checks section shows verdict badges, retry counts, and artifact refs with no dependency on task-run logs.

**Acceptance Scenarios**:

1. **Given** a step row has approval-policy check data, **When** the operator expands the row, **Then** the Checks section shows the verdict badge, summary, retry count, and linked review artifact ref.
2. **Given** the step is still under review, **When** the row renders, **Then** the Steps surface shows `reviewing` state and pending-review copy without waiting for execution-wide logs.
3. **Given** a row has no review evidence artifact, **When** the row renders, **Then** the Checks section remains stable and explicitly shows that no review artifact is linked yet.

### Edge Cases

- What happens when approval policy is absent or disabled? The execution path remains unchanged and `checks[]` stays empty unless another producer populates it.
- What happens when a tool type is exempt from review? The step should not enter `reviewing` and no approval-policy check row should be created.
- What happens when the review activity fails transiently? Temporal activity retry handles the activity failure; the step row should not fabricate a final verdict.
- What happens when a step already failed execution before review could run? The workflow records the execution failure as before and does not create a fake passed review row.

## Requirements

### Functional Requirements

- **FR-001**: System MUST invoke structured approval-policy review from `MoonMind.Run` for eligible completed step executions and represent that work with step status `reviewing`. Mappings: DOC-REQ-001, DOC-REQ-004, DOC-REQ-008.
- **FR-002**: System MUST populate `checks[]` with an `approval_policy` row carrying verdict state, bounded summary, `retryCount`, and optional `artifactRef`, instead of requiring log parsing. Mappings: DOC-REQ-002, DOC-REQ-005.
- **FR-003**: System MUST keep full review feedback/issues outside workflow state by writing review evidence to artifacts and linking that artifact through `checks[].artifactRef`. Mappings: DOC-REQ-003.
- **FR-004**: System MUST retry eligible steps after failed reviews by injecting review feedback into the rerun while keeping the latest/current attempt state on the same logical step row. Mappings: DOC-REQ-001, DOC-REQ-004.
- **FR-005**: System MUST treat `INCONCLUSIVE` review outcomes as accepted execution while still surfacing the actual review result in step evidence. Mappings: DOC-REQ-004, DOC-REQ-005.
- **FR-006**: System MUST render approval-policy check data in the existing Mission Control Steps surface with compact badges, retry count copy, and review artifact refs in the Checks section. Mappings: DOC-REQ-005, DOC-REQ-006, DOC-REQ-007.
- **FR-007**: System MUST add workflow-boundary tests for `reviewing` transitions, final verdict/check state, retry count behavior, and bounded artifact-backed review evidence. Mappings: DOC-REQ-008.
- **FR-008**: System MUST implement production runtime code changes plus validation tests in this phase; documentation-only work is insufficient. Mappings: DOC-REQ-001 through DOC-REQ-008.

### Key Entities

- **ApprovalPolicyStepCheck**: The `checks[]` row for approval-policy review, carrying verdict state, bounded summary, retry count, and optional evidence artifact ref.
- **ReviewEvidenceArtifact**: The durable JSON artifact containing full review request/verdict/issue payloads that stay outside workflow state.
- **ReviewRetryState**: Workflow-owned state that tracks current review feedback and retry count for one logical step attempt sequence.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Workflow-boundary tests prove eligible steps transition through `reviewing` and end with structured `approval_policy` check rows.
- **SC-002**: Workflow-boundary tests prove a FAIL→PASS review sequence increments step attempt, exposes retry count, and stores full review evidence in an artifact instead of workflow state.
- **SC-003**: UI tests prove the expanded Checks section shows verdict badges, retry counts, and review artifact refs without requiring task-run observability fetches.
- **SC-004**: `pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q`, `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` pass.

## Assumptions

- Phase 5 focuses on reconciling review/check state into the existing ledger and UI, not on building a richer cross-run review history surface.
- The placeholder `step.review` activity may continue using mocked/pass-through behavior in isolated activity tests, while workflow-boundary tests stub verdicts directly.
- The Phase 3/4 execution and task-detail contracts remain authoritative; this phase extends them rather than redefining step identity or latest-run semantics.
