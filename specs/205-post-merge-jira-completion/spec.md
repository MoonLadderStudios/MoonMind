# Feature Specification: Post-Merge Jira Completion

**Feature Branch**: `205-post-merge-jira-completion`  
**Created**: 2026-04-17  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-403 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-403 from MM project
Summary: Post-merge Jira completion
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-403 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-403: Post-merge Jira completion

Summary

Implement reliable post-merge Jira completion for MoonMind tasks that are Jira-backed and use `pr-resolver` / merge automation.

When a task is truly Jira-related and the PR is successfully merged via MoonMind's merge automation flow, MoonMind should transition the correct Jira issue to a terminal done state after merge success is verified, not merely when `pr-resolver` is configured or launched.

This work should reuse MoonMind's existing trusted Jira integration layer and Temporal merge-automation lifecycle rather than pushing Jira mutation logic into the `pr-resolver` shell scripts.

Current State

MoonMind already has most of the primitives needed, but the final Jira completion behavior is not implemented.

Relevant existing behavior:

- `MoonMind.Run` starts and waits on `MoonMind.MergeAutomation` for PR-publishing tasks with merge automation enabled.
- `MoonMind.MergeAutomation` waits for readiness, launches a resolver child `MoonMind.Run`, and returns terminal outcomes such as `merged`, `already_merged`, `blocked`, `failed`, etc.
- `build_resolver_run_request()` already passes an optional `jiraIssueKey` into the resolver child skill args.
- `merge_automation.evaluate_readiness` already checks Jira status as a pre-merge gate when Jira gating is enabled.
- `JiraToolService` already supports `get_issue`, `search_issues`, `get_transitions`, and `transition_issue`.
- `pr-resolver` itself currently merges PRs and writes artifacts, but it does not mutate Jira.

That means the missing capability is not a brand-new Jira integration. The missing capability is reliable orchestration and issue-resolution logic for a post-merge transition.

Problem

Today MoonMind can use Jira as a readiness gate, but it does not automatically mark the Jira issue done after merge success.

There are several reliability risks if this is implemented naively:

- The system could transition Jira too early, for example when `pr-resolver` is activated rather than when merge succeeds.
- The system could transition the wrong Jira issue when the issue key is absent or ambiguous.
- The system could guess a transition incorrectly when a workflow has multiple done-like transitions.
- The system could produce non-idempotent behavior on retries or replay.
- The system could bury post-merge Jira failures inside a lower-level script instead of surfacing them through MoonMind's workflow artifacts and operator-visible state.

Desired Outcome

For Jira-backed workflows:

- If merge automation reaches `merged` or `already_merged`, MoonMind should resolve the correct Jira issue key and transition that issue into a terminal done state.
- The transition should occur inside MoonMind's trusted backend / activity path, not via raw Jira credentials in the agent shell.
- The system should not guess when the issue target is ambiguous.
- The behavior should be idempotent and safe across retries, workflow replay, and duplicate signals.
- Operators should be able to see which Jira issue was selected, how it was selected, what transition was used, and whether the post-merge action succeeded.

Design Decision

Implement post-merge Jira completion as a Merge Automation success side effect owned by `MoonMind.MergeAutomation`.

Do not put Jira transition logic inside the `pr-resolver` scripts.

Rationale:

- `MoonMind.MergeAutomation` already owns the meaning of "the PR is actually finished."
- It already carries `jiraIssueKey` in its contract and already uses Jira as a readiness gate.
- The trusted Jira tool path already exists in MoonMind.
- This keeps `pr-resolver` focused on PR diagnosis / remediation / merge and keeps Jira completion inside the workflow orchestration layer where retries, artifacts, and visibility are already handled.

Scope

In scope:

- Resolve the correct Jira issue for a Jira-backed merge-automation run.
- Transition that issue after merge success.
- Define strict precedence rules for determining the target Jira issue.
- Define strict rules for selecting the transition to execute.
- Make the behavior idempotent and observable.
- Add tests for normal, missing, conflicting, and ambiguous cases.
- Update docs for merge automation and Jira integration.

Out of scope:

- General-purpose arbitrary "complete multiple Jira issues from one PR" behavior.
- Fuzzy / semantic search over Jira summaries to guess a target without strong evidence.
- Broad UI redesign beyond the minimum needed to surface and optionally configure the behavior.

Proposed Behavior

1. Run only after true merge success

The post-merge Jira action should only run when `MoonMind.MergeAutomation` reaches a resolver disposition of:

- `merged`
- `already_merged`

It must not run merely because:

- `pr-resolver` is present in the plan,
- merge automation is enabled,
- the resolver child was launched,
- the workflow is only waiting on checks/reviews/Jira gating.

2. Only run for Jira-backed workflows

The post-merge Jira action should only run when the workflow is determined to be Jira-backed.

This should be true when any of the following are true:

- a canonical Jira issue key was already captured for the run,
- explicit merge automation Jira completion config is enabled,
- the task origin metadata indicates a Jira-backed origin,
- the workflow was created by a Jira-related workflow / preset and produced a canonical Jira key.

3. Resolve one authoritative Jira issue key

MoonMind must resolve exactly one Jira issue key before attempting a transition.

If there is no authoritative issue key, or if multiple plausible issue keys remain after validation, MoonMind must not transition any issue and should surface an operator-actionable blocked/failure reason.

Jira Issue Resolution Strategy

Implement a dedicated helper / service for canonical post-merge Jira issue resolution.

Suggested output contract:

```json
{
  "status": "resolved|missing|ambiguous|invalid",
  "issueKey": "MM-123",
  "source": "explicit_post_merge|merge_automation|task_origin|task_metadata|publish_context|pr_metadata",
  "candidates": [
    {
      "issueKey": "MM-123",
      "source": "merge_automation",
      "validated": true
    }
  ],
  "reason": null
}
```

Candidate source precedence, highest to lowest:

1. Explicit post-merge config, for example `mergeAutomation.postMergeJira.issueKey` or equivalent. If present, this wins.
2. Existing merge automation Jira key, such as `mergeAutomation.jiraIssueKey`, `mergeAutomationConfig.gate.jira.issueKey`, or anything already normalized into the `MoonMind.MergeAutomation` input.
3. Canonical task origin / task metadata. A Jira-backed task should capture its authoritative source issue key early in the root run and preserve it durably. This is the preferred automatic path.
4. Publish context / root workflow context. If the root workflow or publish context already persisted a Jira key, use it.
5. PR metadata fallback, strict only. Extract exact issue keys from the PR title, PR body, and branch name using a Jira issue-key regex.

Validation rules:

- Every candidate key must be validated via `JiraToolService.get_issue()` before it becomes authoritative.
- Validation should ensure the key exists, is allowed by Jira policy, belongs to an allowed project, and can be fetched through the trusted Jira service.

Ambiguity rules:

- If multiple different validated issue keys are found across sources, the resolution result is ambiguous.
- In that case, do not transition anything, record the candidate keys and sources in artifacts / summary, and fail or block the post-merge Jira step in a way that is operator-visible.

Important reliability rule:

Do not use fuzzy Jira summary search as a default fallback. `search_issues()` exists, but using free-form summary / semantic matching to guess the issue after merge is too risky for this feature. A later enhancement can consider narrowly scoped exact-match search only when the project is unambiguous and the search signal is very strong, but v1 should prefer explicit / captured / regex-extracted exact keys only.

Transition Selection Strategy

Selecting the Jira issue is only half of the problem. The system must also select the correct transition safely.

Implement a dedicated transition selector with this precedence:

1. Explicit transition ID override. If configured, use it after validating it is currently available.
2. Explicit transition name override. Use case-insensitive exact match against available transitions.
3. Default automatic strategy: choose one done-category transition.

For the automatic strategy:

- Fetch transitions via `JiraToolService.get_transitions()`.
- If the issue is already in a done-category status, treat as success no-op.
- Otherwise find transitions whose target status belongs to Jira status category `done`.
- If exactly one such transition exists, use it.
- If zero or more than one such transitions exist, treat as unresolved and require operator review/configuration.

Required fields / resolution handling:

- Some Jira workflows require fields on transition, for example resolution.
- The implementation should request transitions with field expansion when needed, support optional configuration for default transition fields, and fail clearly when required transition fields are missing rather than guessing.

Workflow Placement

Recommended implementation point:

Add the post-merge Jira transition inside `MoonMind.MergeAutomation` after resolver success is confirmed and before the workflow returns terminal success.

Pseudo-flow:

1. Merge gate opens.
2. Resolver child run executes.
3. Resolver returns `merged` or `already_merged`.
4. Merge automation resolves canonical Jira issue key.
5. Merge automation selects valid done transition.
6. Merge automation performs Jira transition.
7. Merge automation writes summary/artifacts.
8. Merge automation returns success to parent.

Why here:

- parent success means implementation + publish + merge automation + Jira completion all succeeded,
- the Jira transition happens at the same durable orchestration boundary that already owns merge completion,
- failures can be surfaced through the existing merge automation panel / artifacts.

Data Model / Contract Changes

Update merge automation contract to support post-merge Jira completion explicitly.

Suggested shape:

```json
{
  "mergeAutomation": {
    "enabled": true,
    "jiraIssueKey": "MM-123",
    "postMergeJira": {
      "enabled": true,
      "issueKey": null,
      "transitionId": null,
      "transitionName": null,
      "strategy": "done_category",
      "required": true,
      "fields": {
        "resolution": {
          "name": "Done"
        }
      }
    }
  }
}
```

Notes:

- `required=true` means failure to resolve / transition Jira should fail or block merge automation terminal success.
- Default behavior for Jira-backed workflows can set this automatically without requiring an operator to configure it manually every time.

Also add canonical workflow metadata fields such as:

- `resolvedJiraIssueKey`
- `resolvedJiraIssueKeySource`
- `postMergeJiraStatus`
- `postMergeJiraTransitionId`
- `postMergeJiraTransitionName`
- `postMergeJiraSummary`

Technical Work Breakdown

1. Add canonical Jira target resolution helper.
   Create a dedicated backend helper/service for resolving the post-merge Jira issue key. It should gather candidate keys from ordered sources, validate each candidate with Jira, deduplicate candidates, return `resolved`, `missing`, `ambiguous`, or `invalid`, and provide structured evidence for UI/artifacts.

2. Capture authoritative Jira issue metadata earlier in the run.
   The root workflow should preserve the most authoritative Jira issue key as early as possible instead of depending on late heuristics. Inspect current task creation / Jira-backed workflow paths, standardize a canonical field in normalized run parameters / task metadata, and propagate it into merge automation input and publish context.

3. Extend merge automation config models.
   Update the Temporal schema models to support post-merge Jira completion config and status.

4. Extend `MoonMind.Run` merge automation request building.
   Update request normalization / payload building so the post-merge Jira config and canonical Jira issue key are passed into `MoonMind.MergeAutomation`.

5. Add post-merge Jira transition execution to `MoonMind.MergeAutomation`.
   On `merged` / `already_merged`, resolve the authoritative Jira issue key, choose the transition, execute the transition, and record outcome in summary / artifacts / memo.

6. Reuse existing trusted Jira tool layer.
   Do not add a second Jira client stack. Reuse `JiraToolService.get_issue`, `JiraToolService.search_issues` only if a tightly constrained exact fallback is explicitly approved, `JiraToolService.get_transitions`, and `JiraToolService.transition_issue`.

7. Implement strict transition selector.
   Create a reusable helper that validates explicit `transitionId`, resolves explicit `transitionName`, detects already-done as no-op success, selects exactly one done-category transition when using auto mode, and refuses ambiguous choices.

8. Add observability and artifacts.
   Artifacts / summary should show resolved Jira issue key, source of issue-key resolution, candidate keys considered, selected transition, whether the issue was already done, whether the transition succeeded, and failure reason if blocked.

Suggested artifacts:

- `artifacts/merge_automation/post_merge_jira_resolution.json`
- `artifacts/merge_automation/post_merge_jira_transition.json`

9. Update Mission Control detail surface.
   Show enough information on the execution detail / merge automation panel to explain which Jira issue MoonMind chose, whether the post-merge Jira action ran, whether it was skipped because the issue was already done, and whether it failed due to ambiguity / missing transition / policy denial.

10. Documentation updates.
    Update relevant docs, including at minimum `docs/Tasks/PrMergeAutomation.md`, `docs/Tools/JiraIntegration.md`, and any merge-automation / task-publishing / task-origin docs that describe Jira-backed flows.

Reliability / Idempotency Requirements

This feature must be safe under retries and duplicates.

Idempotency rules:

- If the issue is already in a done-category state when post-merge Jira runs, treat as success no-op.
- If a retry re-runs the same transition logic after a successful transition, do not fail simply because the issue is already done.
- Do not emit duplicate transitions when the workflow replays.
- Use deterministic workflow state + activity execution boundaries rather than non-durable script-local state.

Failure behavior:

If post-merge Jira completion is required for a Jira-backed workflow:

- missing issue key => fail/block merge automation,
- ambiguous issue key => fail/block merge automation,
- no valid done transition => fail/block merge automation,
- Jira permission/policy denial => fail/block merge automation,
- Jira transient outage => retry through activity retry policy and then fail/block with clear evidence.

Edge Cases

The implementation must explicitly handle:

1. PR merged successfully but Jira issue already in done-category status.
2. PR merged successfully but no Jira issue key can be resolved.
3. PR metadata contains multiple issue keys.
4. Configured Jira issue key conflicts with PR-derived issue key.
5. Resolver says `already_merged` after a previous attempt.
6. Workflow retry / replay after Jira transition already happened.
7. Jira workflow exposes multiple done-category transitions.
8. Jira transition requires fields that were not configured.
9. Jira issue belongs to a disallowed project.
10. Jira-backed task depends on another task and inherits or propagates wrong issue context.

Testing Requirements

Unit tests:

- issue-key candidate extraction,
- precedence ordering,
- candidate validation,
- ambiguity detection,
- transition selection,
- already-done no-op behavior,
- missing / conflicting / invalid config.

Workflow tests:

- merge automation success => Jira transition success,
- merge automation success => already-done no-op,
- merge automation success => ambiguous issue key => blocked/failed,
- merge automation success => no transition available => blocked/failed,
- replay/idempotent retry behavior,
- parent run terminal state propagation when post-merge Jira completion fails.

Integration tests:

- `get_issue`,
- `get_transitions`,
- `transition_issue`,
- required transition field failure,
- permission-denied failure.

Acceptance Criteria

- [ ] A Jira-backed merge-automation run transitions the correct Jira issue only after PR merge success is confirmed.
- [ ] The implementation reuses MoonMind's trusted Jira tool layer rather than embedding Jira credentials into agent shells.
- [ ] The system resolves one authoritative Jira issue key using a deterministic precedence order.
- [ ] If zero or multiple valid issue keys remain, MoonMind does not transition any Jira issue and surfaces an explicit operator-visible failure/blocked reason.
- [ ] The system selects a valid done transition safely and does not guess when multiple done-category transitions exist.
- [ ] If the Jira issue is already done, the action is treated as success no-op.
- [ ] The behavior is idempotent under retries/replay.
- [ ] Merge automation artifacts / summary include Jira resolution and transition evidence.
- [ ] Documentation is updated to describe the feature and its resolution rules.

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## Classification

- Input class: single-story feature request.
- Mode: runtime.
- Source design treatment: the MM-403 Jira preset brief is a runtime source requirement for post-merge Jira completion behavior, not a docs-only target.
- Resume decision: no existing `MM-403` spec artifacts were present, so the workflow starts at Specify.

## User Story - Complete Jira After Merge

**Summary**: As a Jira-backed workflow operator, I want MoonMind to complete the correct Jira issue only after merge automation verifies merge success so that issue status reflects delivered work without premature or ambiguous transitions.

**Goal**: Jira-backed PR publishing runs can automatically and safely move one authoritative Jira issue to a terminal done state after merge success, while blocking or no-oping when the target issue or transition cannot be selected safely.

**Independent Test**: Can be fully tested by running merge automation scenarios with trusted Jira responses for successful merge, already-merged, already-done, missing issue key, ambiguous issue keys, unavailable done transition, and required transition fields, then verifying the final workflow outcome, Jira transition decision, and operator-visible artifacts for each scenario.

**Acceptance Scenarios**:

1. **Given** a Jira-backed run has one authoritative Jira issue key and merge automation returns `merged`, **When** post-merge completion runs, **Then** the system validates the issue, selects exactly one safe done transition, transitions that issue, and records the result for operators.
2. **Given** a Jira-backed run has one authoritative Jira issue key and merge automation returns `already_merged`, **When** post-merge completion runs, **Then** the system performs the same Jira completion checks before reporting the merge automation run as successful.
3. **Given** the target Jira issue is already in a done-category status, **When** post-merge completion runs, **Then** the system treats completion as a successful no-op and records that no transition was needed.
4. **Given** the workflow cannot resolve exactly one valid Jira issue key, **When** merge automation reaches a merge-success disposition, **Then** the system does not transition any Jira issue and surfaces a blocked or failed outcome with candidate-key evidence.
5. **Given** the target Jira workflow exposes zero or multiple done-category transitions and no explicit safe transition is configured, **When** post-merge completion evaluates transitions, **Then** the system refuses to guess and surfaces an operator-visible blocked or failed outcome.
6. **Given** a replay, retry, or duplicate signal occurs after a prior successful Jira completion, **When** post-merge completion is evaluated again, **Then** the system remains idempotent and does not emit duplicate Jira transitions.

### Edge Cases

- Merge automation is enabled but the resolver child has not reached `merged` or `already_merged`.
- The workflow was started from Jira but the authoritative issue key is absent from normalized run or merge automation state.
- PR metadata contains one or more issue keys that conflict with configured or captured Jira issue context.
- Jira policy denies fetching or transitioning the candidate issue.
- The Jira issue belongs to a disallowed or unexpected project.
- A done transition requires fields that were not configured.
- Jira is temporarily unavailable after merge success.
- A retry observes the issue already done after a prior successful transition.

## Assumptions

- A terminal done state means a Jira status in the done status category unless an explicit transition name or ID is configured and validated.
- Post-merge Jira completion is required only for runs that MoonMind can classify as Jira-backed.
- Exact issue-key extraction from PR metadata is acceptable only as a strict fallback after stronger configured or captured sources are evaluated.
- Jira service outages should use the workflow/activity retry policy before surfacing a terminal blocked or failed outcome.

## Source Design Requirements

- **DESIGN-REQ-001** (Source: MM-403 Jira brief, Desired Outcome): A Jira-backed merge-automation run MUST complete the correct Jira issue only after merge success is verified. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-011.
- **DESIGN-REQ-002** (Source: MM-403 Jira brief, Design Decision): Post-merge Jira completion MUST be owned by the merge automation lifecycle rather than the lower-level resolver script. Scope: in scope. Maps to FR-001, FR-014.
- **DESIGN-REQ-003** (Source: MM-403 Jira brief, Scope): The system MUST resolve exactly one authoritative Jira issue key and refuse missing, invalid, or ambiguous targets. Scope: in scope. Maps to FR-004, FR-005, FR-006.
- **DESIGN-REQ-004** (Source: MM-403 Jira brief, Transition Selection Strategy): The system MUST select a done transition safely and refuse to guess when transition selection is ambiguous or unsupported. Scope: in scope. Maps to FR-007, FR-008, FR-009, FR-010.
- **DESIGN-REQ-005** (Source: MM-403 Jira brief, Reliability / Idempotency Requirements): Post-merge Jira completion MUST be idempotent under retries, workflow replay, duplicate signals, and already-done issues. Scope: in scope. Maps to FR-010, FR-011.
- **DESIGN-REQ-006** (Source: MM-403 Jira brief, Desired Outcome and Technical Work Breakdown): Operators MUST be able to see the selected Jira issue, selection source, transition decision, success or failure status, and failure reason. Scope: in scope. Maps to FR-012, FR-013.
- **DESIGN-REQ-007** (Source: MM-403 Jira brief, Acceptance Criteria): The implementation MUST reuse MoonMind's trusted Jira integration path and MUST NOT expose raw Jira credentials through agent shells. Scope: in scope. Maps to FR-014.
- **DESIGN-REQ-008** (Source: MM-403 Jira brief, Scope and Important reliability rule): General-purpose completion of multiple Jira issues and fuzzy Jira summary search are out of scope. Scope: out of scope to keep this story bounded to one authoritative issue. Maps to FR-015.
- **DESIGN-REQ-009** (Source: MM-403 Jira brief, Testing Requirements): Verification MUST cover normal, missing, conflicting, ambiguous, no-transition, already-done, and retry/replay cases. Scope: in scope. Maps to FR-016.
- **DESIGN-REQ-010** (Source: MM-403 Jira brief, traceability instruction): MoonSpec artifacts and delivery metadata MUST preserve Jira issue key MM-403 and the original preset brief for final verification. Scope: in scope. Maps to FR-017.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST run post-merge Jira completion only after merge automation reaches a verified merge-success disposition.
- **FR-002**: System MUST treat `merged` and `already_merged` merge automation dispositions as eligible for post-merge Jira completion.
- **FR-003**: System MUST NOT run post-merge Jira completion merely because merge automation is enabled, a resolver was launched, or a readiness gate is waiting.
- **FR-004**: System MUST determine whether a run is Jira-backed before attempting Jira completion.
- **FR-005**: System MUST resolve one authoritative Jira issue key using deterministic source precedence.
- **FR-006**: System MUST validate every candidate Jira issue key through the trusted Jira integration before selecting it as authoritative.
- **FR-007**: If zero valid issue keys or multiple different valid issue keys remain, the system MUST NOT transition Jira and MUST produce an operator-visible blocked or failed outcome.
- **FR-008**: System MUST treat an already-done target Jira issue as a successful no-op completion.
- **FR-009**: System MUST select a Jira completion transition only when an explicit configured transition is currently valid or exactly one available transition targets a done-category status.
- **FR-010**: If no safe completion transition is available, multiple completion transitions are available, or required transition fields are missing, the system MUST NOT guess and MUST produce an operator-visible blocked or failed outcome.
- **FR-011**: Post-merge Jira completion MUST be idempotent across workflow retries, replay, duplicate signals, and repeated observation of an already-completed Jira issue.
- **FR-012**: System MUST record completion evidence that includes the selected Jira issue key, issue-key source, candidate keys considered, selected transition or no-op reason, completion status, and failure reason when applicable.
- **FR-013**: Operator-visible run or merge automation summaries MUST expose whether post-merge Jira completion succeeded, no-oped, was skipped, or blocked final success.
- **FR-014**: Jira fetching and transition operations MUST use MoonMind's trusted Jira integration boundary and MUST NOT require raw Jira credentials in agent shells or resolver scripts.
- **FR-015**: System MUST NOT use fuzzy Jira summary search or complete multiple Jira issues by default for this feature.
- **FR-016**: Verification MUST cover successful transition, already-done no-op, missing issue key, ambiguous issue keys, unavailable completion transition, required transition field failure, and retry/replay behavior.
- **FR-017**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-403` and the original Jira preset brief.

### Key Entities

- **Post-Merge Jira Completion Decision**: The outcome of evaluating whether a Jira issue should be transitioned after merge success, including selected issue, transition choice, no-op state, and failure reason.
- **Jira Issue Candidate**: A possible Jira issue key discovered from configured completion input, merge automation state, task origin metadata, publish context, or strict PR metadata extraction.
- **Completion Transition Candidate**: A currently available Jira transition that may move the selected issue to a terminal done state.
- **Completion Evidence**: Operator-visible summary and artifact data that explains the target issue, issue-key source, candidate keys, selected transition, idempotency/no-op state, and any blocker.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Workflow or boundary tests verify a Jira-backed merge automation success transitions exactly one validated Jira issue after `merged`.
- **SC-002**: Workflow or boundary tests verify `already_merged` triggers the same post-merge Jira completion behavior as `merged`.
- **SC-003**: Tests verify an already-done Jira issue completes as a no-op without requiring another transition.
- **SC-004**: Tests verify missing, invalid, or ambiguous Jira issue keys prevent Jira mutation and produce an operator-visible blocked or failed outcome.
- **SC-005**: Tests verify zero, multiple, or field-incomplete done transitions prevent Jira mutation and produce an operator-visible blocked or failed outcome.
- **SC-006**: Tests verify retry/replay or duplicate completion evaluation does not produce duplicate Jira transitions.
- **SC-007**: Test or artifact assertions verify completion evidence includes selected issue, source, candidates, transition/no-op decision, status, and failure reason.
- **SC-008**: Static or unit checks verify Jira completion uses the trusted Jira integration boundary and does not add raw Jira credential use to agent shell or resolver script paths.
- **SC-009**: Final verification confirms `MM-403` and the original Jira preset brief are preserved in active MoonSpec artifacts and delivery metadata.
