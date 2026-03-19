# Feature Specification: Step Review Gate

**Feature Branch**: `086-step-review-gate`
**Created**: 2026-03-18
**Status**: Draft
**Input**: User description: "Fully implement docs/Tasks/StepReviewGateSystem.md"

## Source Document Requirements

Source: `docs/Tasks/StepReviewGateSystem.md`

| ID | Source Section | Requirement Summary |
|---|---|---|
| DOC-REQ-001 | §4.1 PlanPolicy Extension | System must define a `ReviewGatePolicy` dataclass with `enabled`, `max_review_attempts`, `reviewer_model`, `review_timeout_seconds`, `skip_tool_types` fields |
| DOC-REQ-002 | §4.1 PlanPolicy Extension | `PlanPolicy` must accept an optional `review_gate` field defaulting to disabled |
| DOC-REQ-003 | §4.2 Plan JSON Schema | Plan JSON must accept a `policy.review_gate` object; missing or `enabled: false` must behave identically to current system |
| DOC-REQ-004 | §4.3 Review Activity Contracts | A `ReviewRequest` input contract must carry node context, step inputs, execution result, and previous feedback |
| DOC-REQ-005 | §4.3 Review Activity Contracts | A `ReviewVerdict` output contract must return verdict (`PASS`/`FAIL`/`INCONCLUSIVE`), confidence, feedback, and issues |
| DOC-REQ-006 | §5.1 Modified Execution Loop | `_run_execution_stage` must wrap each node in a review-retry loop when the gate is enabled |
| DOC-REQ-007 | §5.1 Modified Execution Loop | Steps must retry up to `max_review_attempts` when the review verdict is `FAIL` |
| DOC-REQ-008 | §5.1 Modified Execution Loop | `INCONCLUSIVE` verdicts must be treated as `PASS` |
| DOC-REQ-009 | §5.2 Feedback Injection | Feedback from failed reviews must be injected into step inputs as `_review_feedback` for skill steps |
| DOC-REQ-010 | §5.2 Feedback Injection | Feedback for agent_runtime steps must be appended to the instruction text |
| DOC-REQ-011 | §6 Review Activity | A `step.review` activity must be registered and routed to the LLM fleet |
| DOC-REQ-012 | §6.2 Activity Implementation | The review activity must construct a prompt, call the configured LLM, and parse the response into a `ReviewVerdict` |
| DOC-REQ-013 | §7 Configuration | Toggle precedence must be: plan-level > workflow-level > env var > default (off) |
| DOC-REQ-014 | §7.3 Environment Variable | `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED` env var must control system-wide default |
| DOC-REQ-015 | §8 Observability | Workflow memo must reflect review cycle state during step execution |
| DOC-REQ-016 | §8.4 Finish Summary | `reports/run_summary.json` must include `reviewGate` metrics |
| DOC-REQ-017 | §9.2 Interaction with Existing Policies | Review gate must compose correctly with `FAIL_FAST` and `CONTINUE` failure modes |
| DOC-REQ-018 | §9.3 Skip List | `skip_tool_types` must exempt specified tool types from review |
| DOC-REQ-019 | §10 Determinism | Review-retry loop must be fully deterministic and replay-safe in Temporal |
| DOC-REQ-020 | §10.3 History Size | Review activities must not exceed Temporal history size limits for typical plans |

## User Scenarios & Testing

### User Story 1 - Enable Review Gate for a Workflow (Priority: P1)

An operator creates a new task in Mission Control and enables the review gate toggle. The system automatically validates each step's output against its inputs using an LLM reviewer. Steps that fail review are retried with feedback.

**Why this priority**: Core value proposition — the fundamental toggle-and-review capability.

**Independent Test**: Can be tested by creating a `MoonMind.Run` workflow with `review_gate.enabled: true` in plan policy and verifying review activities execute after each step.

**Acceptance Scenarios**:

1. **Given** a plan with `review_gate.enabled: true` and 3 skill nodes, **When** the workflow executes, **Then** a `step.review` activity runs after each of the 3 step executions
2. **Given** a plan with `review_gate` absent, **When** the workflow executes, **Then** no review activities run and behavior is identical to current system
3. **Given** a step that produces output the reviewer judges as `PASS`, **When** the review completes, **Then** the workflow moves to the next step without retry

---

### User Story 2 - Retry Failed Steps with Feedback (Priority: P1)

When the LLM reviewer determines a step did not achieve its aims, the step is retried with structured feedback so the executing agent can self-correct.

**Why this priority**: Review without retry provides diagnostic value but the self-correction loop is the key differentiator.

**Independent Test**: Can be tested by mocking a review that returns `FAIL` on the first attempt and `PASS` on the second, verifying the step receives feedback and the retry succeeds.

**Acceptance Scenarios**:

1. **Given** a step with `max_review_attempts: 2` whose first execution fails review, **When** it is retried with feedback, **Then** the step inputs include `_review_feedback` with the reviewer's feedback
2. **Given** a step that fails all review attempts, **When** `failure_mode` is `FAIL_FAST`, **Then** the workflow halts with a clear error
3. **Given** a step that fails all review attempts, **When** `failure_mode` is `CONTINUE`, **Then** the workflow proceeds to the next step

---

### User Story 3 - Configure Review Gate Parameters (Priority: P2)

An operator configures review gate parameters (max attempts, reviewer model, timeout, skip list) at plan level, workflow level, or via env var. The system respects the configured precedence.

**Why this priority**: Configuration flexibility enables cost control and customization.

**Independent Test**: Can be tested by setting `review_gate` at different precedence levels and verifying correct resolution.

**Acceptance Scenarios**:

1. **Given** a plan with `review_gate.max_review_attempts: 3`, **When** the workflow executes, **Then** steps are retried up to 3 times on review failure
2. **Given** a plan without `review_gate` and workflow-level `reviewGate.enabled: true`, **When** the workflow executes, **Then** the review gate is active
3. **Given** `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED=true` and no plan/workflow override, **When** a workflow executes, **Then** the review gate is active

---

### User Story 4 - Observe Review Results (Priority: P2)

An operator monitors a running workflow in Mission Control and can see review verdicts, retry progress, and final review gate metrics in the finish summary.

**Why this priority**: Observability is essential for operators to trust and debug the system.

**Independent Test**: Can be tested by checking workflow memo updates and finish summary JSON after a review-gated execution.

**Acceptance Scenarios**:

1. **Given** a running workflow with review gate enabled, **When** a step is under review, **Then** the workflow memo shows "Step N/M: tool_name (review attempt X/Y)"
2. **Given** a completed workflow with review gate, **When** the finish summary is retrieved, **Then** it includes `reviewGate.stepsReviewed`, `reviewGate.totalReviewAttempts`, and per-outcome counts

---

### Edge Cases

- What happens when the review activity itself fails (LLM error/timeout)? → Temporal activity retry policy handles transient failures; infrastructure errors do not count as review failures.
- What happens with `max_review_attempts: 0`? → Treated as review gate disabled for that step (execute once, no review).
- What happens when `skip_tool_types` includes `"agent_runtime"` but a node is of that type? → Node executes without review.
- What happens when `INCONCLUSIVE` is returned? → Treated as `PASS`; workflow proceeds.

## Requirements

### Functional Requirements

- **FR-001**: System MUST add `ReviewGatePolicy` with fields `enabled` (default false), `max_review_attempts` (default 2), `reviewer_model` (default "default"), `review_timeout_seconds` (default 120), `skip_tool_types` (default empty) → DOC-REQ-001
- **FR-002**: System MUST extend `PlanPolicy` with an optional `review_gate` field defaulting to `ReviewGatePolicy()` → DOC-REQ-002
- **FR-003**: Plan JSON parsing MUST accept and validate the `policy.review_gate` block; absent/disabled must produce identical behavior to current system → DOC-REQ-003
- **FR-004**: System MUST define `ReviewRequest` and `ReviewVerdict` data contracts → DOC-REQ-004, DOC-REQ-005
- **FR-005**: `_run_execution_stage` MUST wrap each plan node in a review-retry loop when `review_gate.enabled` is true and the node's tool type is not in `skip_tool_types` → DOC-REQ-006, DOC-REQ-018
- **FR-006**: Steps MUST retry up to `max_review_attempts` when the verdict is `FAIL`, and `INCONCLUSIVE` MUST be treated as `PASS` → DOC-REQ-007, DOC-REQ-008
- **FR-007**: Failed-review feedback MUST be injected as `_review_feedback` in skill inputs and appended to instruction text for agent_runtime steps → DOC-REQ-009, DOC-REQ-010
- **FR-008**: System MUST register a `step.review` activity routed to the LLM fleet → DOC-REQ-011
- **FR-009**: The review activity MUST construct a prompt from step context, call the configured LLM, and return a structured `ReviewVerdict` → DOC-REQ-012
- **FR-010**: Configuration precedence MUST be: plan-level > workflow-level initialParameters > `MOONMIND_REVIEW_GATE_DEFAULT_ENABLED` env var > default off → DOC-REQ-013, DOC-REQ-014
- **FR-011**: Workflow memo MUST update to show review cycle state; finish summary MUST include `reviewGate` metrics → DOC-REQ-015, DOC-REQ-016
- **FR-012**: Review gate MUST compose correctly with `FAIL_FAST`/`CONTINUE` failure modes → DOC-REQ-017
- **FR-013**: Review-retry loop MUST be deterministic and replay-safe in Temporal → DOC-REQ-019
- **FR-014**: Review activities MUST not cause Temporal history size concerns for typical plans (≤ 20 steps) → DOC-REQ-020

### Key Entities

- **ReviewGatePolicy**: Configuration object controlling review gate behavior per plan
- **ReviewRequest**: Input envelope for the LLM review activity
- **ReviewVerdict**: Structured output from the LLM review activity containing verdict, confidence, feedback, and issues
- **step.review Activity**: Temporal activity that evaluates step outputs via LLM

## Success Criteria

### Measurable Outcomes

- **SC-001**: A workflow with review gate enabled executes a review activity after every non-exempt step
- **SC-002**: Steps that fail review are retried with feedback up to the configured max, and the retry succeeds when the underlying issue is correctable
- **SC-003**: A workflow with review gate disabled behaves identically to the current system with zero overhead
- **SC-004**: All existing unit tests pass without modification (backward compatibility)
- **SC-005**: Review gate metrics appear correctly in the finish summary JSON

## Assumptions

- The LLM fleet (`mm.activity.llm`) is available and can process review prompts within the configured timeout
- The review activity uses the same LLM infrastructure and routing as existing planning activities
- The `reviewer_model` configuration will initially resolve to the system default model; model routing will leverage existing infrastructure
- Temporal's default history limits (50,000 events) are adequate for typical plans with review gate (worst case: ~40 extra events for a 20-step plan with 2 retries each)
