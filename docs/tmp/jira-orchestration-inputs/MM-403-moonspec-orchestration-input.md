# MM-403 MoonSpec Orchestration Input

## Source

- Jira issue: MM-403
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Post-merge Jira completion
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Normalized detail source: `/api/jira/issues/MM-403`
- Canonical source: `recommendedImports.presetInstructions` from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

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

Implementation Notes / File Targets

Likely file areas impacted:

- `moonmind/schemas/temporal_models.py`
- `moonmind/workflows/temporal/workflows/run.py`
- `moonmind/workflows/temporal/workflows/merge_automation.py`
- `moonmind/workflows/temporal/activity_runtime.py`
- `moonmind/integrations/jira/tool.py` (reuse and possibly helper extensions)
- tests for merge automation and Jira integration
- `docs/Tasks/PrMergeAutomation.md`
- `docs/Tools/JiraIntegration.md`

Recommended Delivery Order

1. Define canonical Jira issue-key resolution contract.
2. Capture/persist authoritative Jira issue key in root task/run metadata.
3. Add post-merge Jira config + schema support.
4. Add transition-selection helper.
5. Add merge-automation post-merge Jira execution.
6. Add observability/artifacts.
7. Add workflow/unit/integration tests.
8. Update docs.
