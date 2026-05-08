# Feature Specification: Observable Remediation Repair and Prevention Lifecycle

**Feature Branch**: `322-remediation-lifecycle-repair-prevention`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-622 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-622 MoonSpec Orchestration Input

## Source

- Jira issue: MM-622
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Run observable remediation lifecycle with repair and prevention outputs
- Labels: moonmind-workflow-mm-d644bfaa-e9fb-4d63-9dff-519fed1a09b7
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-622 from MM project
Summary: Run observable remediation lifecycle with repair and prevention outputs
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 9.8 Immediate repair and prevention workflow
- 13. Runtime lifecycle
- 16.11 Remediation task itself fails
- Appendix C. Design rule summary
Coverage IDs:
- DESIGN-REQ-012
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-025

As a remediation task, I progress through evidence collection, diagnosis, optional approval, action, verification, summary, and lock release while recording whether I repaired the target, escalated, or created a reviewable long-term prevention change.

Acceptance Criteria
- Remediation read models expose bounded remediationPhase values while top-level mm_state remains the normal execution state.
- Immediate repair attempts are the smallest plausible action and are followed by verify_target with repaired/still_failed/not_attempted/unsafe/approval_required/escalated outcomes.
- Recurrence-prevention analysis is recorded whether it creates a PR, reports findings, or determines no reviewable fix exists.
- Cancellation of remediation does not mutate the target except for already-requested actions and still attempts lock release and final audit publication.
- Continue-As-New preserves all remediation-critical refs and budgets.

Requirements
- Lifecycle state is observable and bounded.
- Immediate repair and prevention are separate outputs.
- Runtime transitions are safe across cancellation, rerun, and Continue-As-New.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-622 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

- Input type: Single-story runtime feature request.
- Runtime decision: Jira Orchestrate always runs as a runtime implementation workflow, and `docs/Tasks/TaskRemediation.md` is treated as runtime source requirements.
- Breakdown decision: `moonspec-breakdown` was not run because the MM-622 Jira preset brief defines one independently testable remediation lifecycle story with a single actor, goal, source document, and bounded acceptance criteria.
- Resume decision: No existing Moon Spec artifact set for `MM-622` was found under `specs/`; specification was the first incomplete stage.

## User Story - Observable Remediation Repair and Prevention Lifecycle

**Summary**: As a remediation task, I need to progress through observable lifecycle phases while separately recording immediate repair and recurrence-prevention outcomes, so operators can understand whether the target was repaired, escalated, or turned into a reviewable prevention change.

**Goal**: Remediation executions expose bounded lifecycle state, attempt only safe minimal repairs, verify and record the target outcome, evaluate recurrence prevention, and preserve enough critical refs to remain coherent through cancellation, rerun, and Continue-As-New boundaries.

**Independent Test**: Can be tested by running a remediation lifecycle against controlled target states that cover repaired, still failed, unsafe, approval-required, escalated, canceled, rerun, and Continue-As-New paths, then verifying the read models, decision log, repair result, prevention result, audit publication, lock release, and preserved refs for each path.

**Acceptance Scenarios**:

1. **Given** a remediation task is collecting evidence for a target execution, **When** operators inspect remediation read models, **Then** they see one bounded remediation phase while the top-level execution state remains the normal task state.
2. **Given** a safe minimal repair is plausible, **When** the remediation task acts, **Then** it records the candidate, uses the smallest plausible action, verifies the target, and publishes one of the supported repair outcomes.
3. **Given** a repair is unsafe, denied by policy, requires approval, or exhausts its action budget, **When** the remediation task reaches a decision point, **Then** it records the reason and escalates without broadening into an unrequested destructive action.
4. **Given** a remediation task repaired the target, still failed, or skipped repair, **When** recurrence analysis completes, **Then** the prevention output records whether a reviewable prevention change was created, findings were reported, or no reviewable fix exists.
5. **Given** a remediation task is canceled while active, **When** cancellation handling runs, **Then** the target is not mutated except for already-requested actions and the task still attempts lock release plus final audit publication.
6. **Given** the target run changes or the remediation task continues as new, **When** the remediation summary is produced, **Then** remediation-critical refs, budgets, approval state, action ledger, and pinned target identity remain traceable.

### Edge Cases

- A target becomes healthy before any repair action is attempted; the remediation records no action needed and still evaluates recurrence prevention.
- The remediation has only partial evidence; the lifecycle degrades safely, records evidence degradation, and avoids side effects that require unavailable proof.
- Approval is rejected or times out; the remediation escalates with a recorded approval outcome rather than retrying the action silently.
- A repair action succeeds but verification still fails; the repair result is `still_failed` and recurrence analysis can still produce a prevention output.
- A prevention change is actionable but repository write policy does not allow creating a reviewable change; the prevention result records the blocked reason.
- Continue-As-New occurs after a lock is acquired; lock identity and action ledger continuity are preserved so duplicate mutation is prevented.

## Assumptions

- Existing top-level task execution states remain authoritative for general execution progress; remediation-specific progress is exposed as a subordinate lifecycle field.
- Existing remediation locking, action authorization, artifact redaction, and audit publication policies remain in force for this story.
- Reviewable prevention changes may be code, configuration, documentation, preset, prompt, or Agent Skill changes when policy allows them.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` lines 595-605): Remediation is a two-track workflow that attempts the smallest safe immediate repair, verifies the target outcome, and separately evaluates recurrence prevention. Scope: in scope, mapped to FR-003, FR-004, FR-005, FR-006, FR-007, and FR-008.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` lines 607-614): The remediation decision log records candidates, attempted/skipped/denied/escalated reasons, action and verification refs, recurrence category, prevention change refs, and no-change reasons. Scope: in scope, mapped to FR-005, FR-006, FR-009, and FR-010.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` lines 993-1007): Read models expose bounded `remediationPhase` values without introducing a new top-level execution state machine. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` lines 1008-1047): The lifecycle makes evidence collection, diagnosis, approval, action, verification, resolution, escalation, summary, and lock release observable without requiring one hard-coded universal plan. Scope: in scope, mapped to FR-002, FR-003, FR-011, and FR-012.
- **DESIGN-REQ-005** (`docs/Tasks/TaskRemediation.md` lines 1049-1055): Canceling remediation does not mutate the target except for already-requested actions, and cancellation still attempts lock release plus final audit publication. Scope: in scope, mapped to FR-011 and FR-012.
- **DESIGN-REQ-006** (`docs/Tasks/TaskRemediation.md` lines 1057-1068): Rerun semantics preserve the pinned target run as the diagnosis anchor and record both pinned and resulting runs when an action changes the target run. Scope: in scope, mapped to FR-013.
- **DESIGN-REQ-007** (`docs/Tasks/TaskRemediation.md` lines 1070-1080): Continue-As-New preserves target identity, context refs, lock identity, action ledger, approval state, retry budget state, and live-follow cursor. Scope: in scope, mapped to FR-014.
- **DESIGN-REQ-008** (`docs/Tasks/TaskRemediation.md` lines 1390-1391): Remediation tasks attempt the smallest safe immediate repair and create reviewable long-term prevention changes when actionable and allowed. Scope: in scope, mapped to FR-004, FR-007, and FR-008.
- **DESIGN-REQ-009** (`docs/Tasks/TaskRemediation.md` lines 1476-1487): Stable design rules require artifact-first evidence, typed actions, locking/idempotency/audit, redacted secrets, loop prevention, separate repair/prevention outputs, and corrected-instruction retry provenance. Scope: in scope, mapped to FR-004, FR-005, FR-008, FR-009, FR-010, FR-011, and FR-014.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose remediation-specific lifecycle state as a bounded subordinate phase without replacing or redefining the top-level task execution state.
- **FR-002**: System MUST limit remediation phase values to collecting evidence, diagnosing, awaiting approval, acting, verifying, resolved, escalated, or failed states, or documented equivalents with the same bounded meanings.
- **FR-003**: System MUST make evidence collection, diagnosis, approval gating, action, verification, summary, and lock release observable in remediation outputs.
- **FR-004**: System MUST attempt an immediate repair only when current evidence, fresh target health, allowed actions, and lock state indicate that a bounded repair is safe and plausible.
- **FR-005**: System MUST ensure any immediate repair is the smallest plausible action and does not silently broaden into a full rerun, destructive action, or unrelated mutation.
- **FR-006**: System MUST verify the target after any attempted repair and record one of the supported repair outcomes: repaired, still failed, not attempted, unsafe, approval required, or escalated.
- **FR-007**: System MUST perform recurrence-prevention analysis regardless of whether immediate repair succeeds, fails, is skipped, or is unsafe.
- **FR-008**: System MUST record a prevention output that distinguishes a created reviewable prevention change, reported findings, no reviewable fix, or a policy-blocked prevention change.
- **FR-009**: System MUST record a remediation decision log that captures repair candidates, attempted/skipped/denied/escalated reasons, action result refs, verification refs, recurrence category, and prevention refs or no-change reasons.
- **FR-010**: System MUST preserve corrected-instruction retry provenance as remediation repair context rather than silently mutating the original task input.
- **FR-011**: System MUST keep target mutation boundaries safe during remediation cancellation, including no new target mutation after cancellation except already-requested actions.
- **FR-012**: System MUST attempt best-effort lock release and final audit publication when remediation is canceled, escalated, fails, or resolves.
- **FR-013**: System MUST preserve the pinned target run identity during rerun scenarios and record any resulting run identity when a remediation action intentionally changes the target run.
- **FR-014**: System MUST preserve remediation-critical refs and budgets across Continue-As-New, including target identity, context artifact ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor where present.
- **FR-015**: System MUST preserve Jira issue key `MM-622` and this canonical Jira preset brief in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Remediation Lifecycle State**: The bounded remediation phase and observable lifecycle milestones for a remediation task.
- **Repair Outcome**: The immediate repair decision, action result, verification result, and final repair classification for the target.
- **Prevention Outcome**: The recurrence-prevention decision, including created reviewable change refs, findings, no-fix rationale, or policy-blocked reason.
- **Remediation Decision Log**: The trace of candidates, decisions, action refs, verification refs, recurrence category, and prevention refs or no-change reasons.
- **Continuity State**: The target identity, pinned run identity, remediation context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor that must survive rerun or Continue-As-New boundaries.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of remediation runs expose exactly one bounded remediation phase at every observable lifecycle checkpoint while preserving the normal top-level task state.
- **SC-002**: 100% of attempted repair actions produce a decision-log entry, action result reference, verification reference, and one supported repair outcome.
- **SC-003**: 100% of remediation runs produce a prevention output indicating created reviewable change, findings-only, no reviewable fix, or policy-blocked reason.
- **SC-004**: 100% of cancellation, escalation, failure, and resolution paths attempt lock release and final audit publication, with any skipped publication reason recorded.
- **SC-005**: 100% of rerun and Continue-As-New paths preserve the required remediation-critical refs and budgets in the final summary or continuity evidence.
- **SC-006**: Verification evidence preserves `MM-622`, the canonical Jira preset brief, and in-scope source mappings for DESIGN-REQ-001 through DESIGN-REQ-009.
