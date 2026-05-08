# Feature Specification: Remediation Lock, Ledger, and Loop Guards

**Feature Branch**: `321-remediation-lock-ledger-guards`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-621 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-621 MoonSpec Orchestration Input

## Source

- Jira issue: MM-621
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Add remediation locks, action ledger, and loop prevention
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-621 from MM project
Summary: Add remediation locks, action ledger, and loop prevention
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 6. Core invariants
- 9.7 Evidence freshness before action
- 12. Locking, idempotency, and loop prevention
- 16.6 Lock conflict
- 16.7 Precondition no longer holds
Coverage IDs:
- DESIGN-REQ-011
- DESIGN-REQ-018
- DESIGN-REQ-019
- DESIGN-REQ-025

As the control plane, I prevent concurrent or repeated unsafe remediation mutations by requiring exclusive locks, stable action idempotency, retry budgets, cooldowns, target-change guards, and nested-remediation limits before side effects can run.

Acceptance Criteria
- Only one remediator can hold the default mutation lock for a target execution at a time.
- Lock acquisition and action execution are idempotent under retries and replays.
- A remediation task stops mutating and records a bounded reason after lock loss, material target change, retry-budget exhaustion, or disallowed nested remediation.
- Repeated identical action requests return ledger-backed decisions instead of duplicating side effects.
- Fresh target health is checked immediately before side-effecting action evaluation.

Requirements
- Mutation is exclusive and observable.
- Action idempotency uses a remediation-owned ledger.
- Loop prevention is policy-enforced by default.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-621 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

- Input type: Single-story feature request.
- Selected mode: Runtime implementation workflow.
- Source design: `docs/Tasks/TaskRemediation.md` is treated as runtime source requirements because the brief describes required remediation control-plane behavior.
- Source design path input: `.`.
- Resume decision: No existing Moon Spec artifacts for `MM-621` were found under `specs/`; `Specify` is the first incomplete stage.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable runtime story.

## User Story - Remediation Mutation Safety

**Summary**: As the MoonMind control plane, I want remediation mutations guarded by exclusive locks, stable action idempotency, retry budgets, cooldowns, target-change checks, and nested-remediation limits so concurrent or repeated remediation cannot perform unsafe side effects.

**Goal**: Remediation tasks can diagnose in parallel when allowed, but any side-effecting action against a shared target is preceded by fresh target evidence, owns an exclusive mutation lock, records idempotent ledger-backed decisions, and stops with an explicit bounded outcome when safety preconditions are not satisfied.

**Independent Test**: Submit or simulate multiple remediation action requests for the same target execution, including duplicate requests, stale target evidence, lock conflicts, retry-budget exhaustion, and nested remediation, then verify only one authorized mutation path proceeds and all other cases produce durable bounded decisions without duplicated side effects.

**Acceptance Scenarios**:

1. **Given** two remediation tasks target the same execution for mutation, **When** both attempt to act, **Then** only one holds the default target-execution mutation lock and the other is blocked, downgraded, or fails with an explicit lock-conflict outcome.
2. **Given** a remediation task retries lock acquisition or repeats the same logical action request, **When** the request is replayed or retried, **Then** the system returns the same lock or ledger-backed decision without duplicating the side effect.
3. **Given** a remediation task is about to execute a side-effecting action, **When** the pinned target run, current target run, target state, target summary, or session identity has materially changed, **Then** the action is recorded as no-op or precondition-failed instead of silently succeeding.
4. **Given** retry budget, action-kind attempt limit, or cooldown policy is exhausted, **When** the remediation task evaluates another side-effecting action, **Then** mutation stops and records a bounded reason visible to operators.
5. **Given** a remediation task targets itself or another remediation task while nested remediation is not explicitly allowed, **When** it evaluates side effects, **Then** loop prevention blocks mutation by default and records the decision.
6. **Given** fresh target health cannot be resolved before action evaluation, **When** the task considers a mutation, **Then** it degrades, escalates, or fails with a bounded reason rather than acting on stale assumptions.

### Edge Cases

- A lock holder loses or expires its mutation lock while an action is being evaluated.
- A stale retry arrives after the target has already been repaired or rerun.
- A repeated action request uses the same logical idempotency key but arrives through a different retry or replay path.
- A lock conflict occurs while diagnosis-only activity could still safely continue.
- A high-risk action is requested after the retry budget is already exhausted.

## Assumptions

- The default v1 mutation scope is the target execution with exclusive mode, matching the source design.
- Diagnosis can remain parallelizable where policy allows; this story governs side-effecting mutation paths.
- Operator-facing outcomes use the project's existing task/remediation status and artifact surfaces.

## Source Design Requirements

- **DESIGN-REQ-011** (`docs/Tasks/TaskRemediation.md` section 6, lines 149-187): Remediation must be explicitly marked, target a pinned logical execution/run snapshot, avoid unbounded upstream history in workflow state, keep evidence server-mediated, use typed allowlisted administrative actions, keep side effects idempotent, require exclusive locking for shared-target mutation, redact secrets, disable nested remediation by default, and avoid infinite waits when evidence cannot resolve. Scope: in scope, mapped to FR-001 through FR-013.
- **DESIGN-REQ-018** (`docs/Tasks/TaskRemediation.md` section 9.7, lines 591-594): Before a side-effecting action, remediation must re-read the target's current bounded health view and must not act on stale assumptions. Scope: in scope, mapped to FR-004 and FR-007.
- **DESIGN-REQ-019** (`docs/Tasks/TaskRemediation.md` section 12, lines 900-985): Remediation requires its own lock and action ledger, canonical lock scopes, idempotent acquisition, explicit lock-loss handling, stable action idempotency keys, retry budgets, cooldowns, nested-remediation defaults, and target-change guards. Scope: in scope, mapped to FR-002 through FR-012.
- **DESIGN-REQ-025** (`docs/Tasks/TaskRemediation.md` sections 16.6 and 16.7, lines 1285-1294): Lock conflicts must prevent concurrent mutation by default, and failed preconditions must be surfaced as no-op or precondition-failed rather than silent success. Scope: in scope, mapped to FR-005, FR-007, and FR-010.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST identify a remediation task and its pinned target execution/run snapshot before evaluating any side-effecting remediation action.
- **FR-002**: The system MUST require an exclusive default mutation lock for side-effecting actions against a shared target execution.
- **FR-003**: Lock acquisition MUST be idempotent under retries and replays for the same logical lock holder and target.
- **FR-004**: The system MUST re-read fresh bounded target health immediately before evaluating a side-effecting action.
- **FR-005**: The system MUST prevent concurrent mutation when another remediator owns the target mutation lock by failing fast, downgrading to observe-only, or queuing only when policy explicitly supports it.
- **FR-006**: The system MUST record explicit lock-loss outcomes and MUST NOT continue mutating after the mutation lock is lost.
- **FR-007**: The system MUST compare pinned target identity and current target identity/state before action execution and record no-op or precondition-failed when the target materially changed.
- **FR-008**: Every side-effecting remediation action request MUST carry a stable idempotency key for the logical intended side effect.
- **FR-009**: The remediation action ledger MUST be the duplicate-suppression surface for repeated logical action requests.
- **FR-010**: Repeated identical action requests MUST return the prior ledger-backed decision and MUST NOT duplicate side effects.
- **FR-011**: The system MUST enforce remediation retry budgets, per-action attempt limits, and cooldowns before allowing repeated side-effecting actions.
- **FR-012**: Nested remediation MUST be blocked by default, including self-targeting and remediation-on-remediation targeting, unless policy explicitly allows it.
- **FR-013**: When fresh evidence, lock state, retry budget, cooldown policy, target-change checks, or nested-remediation checks block action, the system MUST record a bounded operator-visible reason.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-621`.

### Key Entities

- **Remediation Task**: A task explicitly marked as remediation, with a target execution/run snapshot and policy-bound authority.
- **Target Execution**: The logical execution and pinned run snapshot that a remediation task diagnoses or mutates.
- **Mutation Lock**: Exclusive permission to perform side-effecting actions for a target scope, including holder identity, target identity, mode, and lifecycle state.
- **Action Ledger Entry**: The durable decision for a logical action request keyed by stable idempotency data, including whether it ran, no-oped, failed, was denied, or reused a previous decision.
- **Safety Decision**: The bounded operator-visible reason produced when a lock, evidence, target-change, budget, cooldown, or nested-remediation guard allows or blocks mutation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In concurrent mutation attempts for the same target execution, zero test cases allow more than one remediator to perform a side-effecting mutation at the same time.
- **SC-002**: In retry and replay scenarios, duplicate logical action requests produce one ledger-backed decision and zero duplicate side effects.
- **SC-003**: In stale-target scenarios, 100% of side-effecting action evaluations perform a fresh target-health check before action execution.
- **SC-004**: In guard-failure scenarios covering lock loss, target change, retry-budget exhaustion, cooldown denial, and nested remediation, 100% produce an operator-visible bounded reason.
- **SC-005**: Traceability evidence preserves `MM-621`, the canonical Jira preset brief, and DESIGN-REQ-011, DESIGN-REQ-018, DESIGN-REQ-019, and DESIGN-REQ-025 in MoonSpec artifacts.
