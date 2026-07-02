# PR Merge Automation - Child Workflow Resolver Strategy

**Status:** Proposed  
**Owner:** MoonMind Platform  
**Audience:** backend, workflow authors, API, Dashboard  
**Related:** `docs/Workflows/WorkflowDependencies.md`, `docs/Workflows/WorkflowPublishing.md`, `docs/Workflows/RequiredCapabilities.md`, `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`, `docs/Temporal/TemporalAgentExecution.md`, `docs/ManagedAgents/SkillGithubPrResolver.md`

---

## 1. Purpose

Define how MoonMind should implement **PR merge automation** for PR-publishing workflow executions using a **child workflow strategy**.

This design covers the case where a workflow execution:

1. performs implementation work,
2. publishes a PR,
3. waits for external merge-readiness signals such as GitHub review/check completion and optional Jira state,
4. invokes `pr-resolver`,
5. does not allow the parent workflow to complete until merge automation reaches its terminal outcome.

Resolver templates that declare `requiredCapabilities` such as `git` and `gh`
must be treated as hard readiness requirements for the child resolver launch.
The canonical declaration, merge, and blocker semantics are defined in
`docs/Workflows/RequiredCapabilities.md`.

The goal is to let downstream workflow dependencies wait on the **original parent Workflow Execution** rather than forcing operators to depend on a second top-level workflow created later.

---

## 2. Design Decision

MoonMind MUST implement PR merge automation as **parent-owned subordinate orchestration** inside the original `MoonMind.UserWorkflow`, using **child workflows**, not a separate top-level dependency target.

This aligns with the current MoonMind dependency contract: workflow dependencies are for **separate top-level `MoonMind.UserWorkflow` executions**, while direct parent-owned subordinate work that should be awaited inside one orchestration history should use **child workflows**.

This also aligns with the Temporal-side lifecycle model: workflows orchestrate, activities do side effects, and MoonMind already treats child workflows as the right durability boundary for subordinate execution concerns.

---

## 3. Goals

- Keep the original workflow's `workflowId` as the only dependency target needed by downstream workflow executions.
- Ensure merge automation is durably awaited before the parent workflow reaches terminal success.
- Avoid a fixed-delay merge strategy; use a **state-based gate** instead.
- Reuse MoonMind's existing execution substrate for `pr-resolver` rather than duplicating skill-execution plumbing.
- Preserve observability, cancellation, artifact output, and replay safety.

---

## 4. Non-Goals

- Introduce merge automation as a separate top-level workflow dependency model.
- Replace `pr-resolver` with a brand-new merge engine.
- Make merge automation editable mid-flight in v1.
- Generalize this document to non-PR publish modes.

---

## 5. Summary of the Strategy

When PR publishing is enabled (`publishMode = "pr"` in `MoonMind.UserWorkflow` parameters) and merge automation is enabled:

1. The original `MoonMind.UserWorkflow` performs its normal implementation work.
2. The publish step creates or updates the PR and emits a durable `PublishContext`.
3. The parent `MoonMind.UserWorkflow` starts a child workflow named **`MoonMind.MergeAutomation`**.
4. The parent workflow does **not** complete while that child workflow is still running.
5. `MoonMind.MergeAutomation` waits on a **merge automation gate**:
   - GitHub external review/check signal completion
   - optional Jira status requirements
6. When the gate opens, `MoonMind.MergeAutomation` starts a **child `MoonMind.UserWorkflow`** dedicated to `pr-resolver`.
7. The resolver child run attempts to remediate and merge.
8. If resolver pushes a new commit and external review/check signal must be re-established, control returns to the gate.
9. If the run is Jira-backed and post-merge Jira completion is enabled, `MoonMind.MergeAutomation` completes the selected Jira issue through the trusted Jira activity path after `merged` or `already_merged`.
10. The parent workflow reaches terminal success only when merge automation returns `merged` or `already_merged` after any required post-merge Jira completion succeeds or no-ops.
11. Terminal `blocked`, `failed`, or `expired` outcomes fail the parent workflow; terminal `canceled` cancels the parent workflow so operator-initiated cancellation is not reported as failure.

---

## 6. Why This Uses Child Workflows

### 6.1 Why not a separate top-level follow-up workflow

A separate top-level follow-up workflow would make the dependency story worse:

- downstream workflow executions would need to know about a later-created second workflow,
- parent-workflow success would no longer mean "implementation + publish + merge automation completed,"
- the relationship would look like a workflow dependency when it is actually parent-owned subordinate work.

MoonMind's dependency contract reserves workflow dependencies for separate top-level runs and treats parent-owned directly awaited subordinate work as child workflow orchestration.

### 6.2 Why not all inside one giant `MoonMind.UserWorkflow`

A single giant workflow would work technically, but it would mix three distinct responsibilities:

- implementation/publish orchestration,
- long-lived external gating,
- repeated resolver execution cycles.

MoonMind's lifecycle model already expects `MoonMind.UserWorkflow` to mix direct activities and child workflows and to move through `awaiting_external` / `awaiting_slot` style waits when subordinate work is in progress. A child workflow boundary keeps the parent readable and keeps the gating logic isolated.

### 6.3 Why the resolver itself is a child `MoonMind.UserWorkflow`

The current MoonMind execution model dispatches plan execution through `agent_runtime` child workflows. `pr-resolver` is an agent skill selected for the resolver run, not a standalone workflow type, and it owns git/PR mutations under `publishMode = "auto"`.

Because of that, `MoonMind.MergeAutomation` SHOULD start a child **`MoonMind.UserWorkflow`** for the resolver, rather than trying to execute the resolver skill directly inside the gate workflow. This reuses:

- existing workspace/runtime setup,
- artifact publishing,
- agent-runtime routing,
- logging and run summaries,
- existing `pr-resolver` contract.

---

## 7. Workflow Topology

```text
MoonMind.UserWorkflow (root parent workflow)
  |- implementation / testing / publish
  |- child: MoonMind.MergeAutomation
  |    |- gate wait / external events / Jira checks
  |    |- child: MoonMind.UserWorkflow (resolver attempt 1)
  |    |     `- executes tool=skill(pr-resolver), publishMode=auto
  |    `- child: MoonMind.UserWorkflow (resolver attempt 2, if needed)
  |          `- executes tool=skill(pr-resolver), publishMode=auto
  `- terminal completion only after MergeAutomation returns success
```

---

## 8. New Workflow Type

MoonMind SHOULD add a new internal workflow type:

- **`MoonMind.MergeAutomation`**

This type is justified because the behavior is distinct:

- it is post-publish orchestration,
- it is long-lived,
- it is callback/poll driven,
- it may execute repeated resolver cycles,
- it is not a normal user workflow surface.

Workflow types should remain few and stable, but new types are appropriate when the behavior is truly distinct.

---

## 9. Parent Workflow Behavior

### 9.1 Parent input contract

Merge automation is configured in the normalized `MoonMind.UserWorkflow` parameters. API or template surfaces may collect this under a nested `task.publish` object, but worker-bound `MoonMind.UserWorkflow` input MUST preserve the current top-level `publishMode` contract:

```json
{
  "publishMode": "pr",
  "mergeAutomation": {
    "enabled": true,
    "strategy": "child_workflow_resolver_v1",
    "resolver": {
      "skill": "pr-resolver",
      "mergeMethod": "squash"
    },
    "gate": {
      "github": {
        "waitForExternalReviewSignal": true,
        "requireStatusChecksReportedOnHead": true,
        "requireNoRunningChecks": true,
        "reviewProviders": []
      },
      "jira": {
        "enabled": false,
        "issueKey": null,
        "allowedStatuses": []
      }
    },
    "timeouts": {
      "fallbackPollSeconds": 120,
      "expireAfterSeconds": 86400
    }
  }
}
```

### 9.2 Parent publish output

The publish step MUST emit a durable `PublishContext` containing at minimum:

- `repository`
- `prNumber`
- `prUrl`
- `baseRef`
- `headRef`
- `headSha`
- `publishedAt`
- optional `jiraIssueKey`

This may be stored as an artifact ref plus compact memo-safe summary fields.
The current `MoonMind.UserWorkflow` publish state tracks a smaller PR summary, so this
feature requires extending that state tracking to include the PR number, current
head SHA, publication timestamp, and artifact ref before `MoonMind.MergeAutomation`
can rely on those fields.

### 9.3 Parent state behavior

After PR publish succeeds and merge automation is enabled, the parent `MoonMind.UserWorkflow`:

1. starts `MoonMind.MergeAutomation`,
2. records the child workflow id in compact metadata,
3. transitions into a waiting posture,
4. does not reach terminal success until the child returns success.

The parent SHOULD use existing state vocabulary rather than inventing a new root state:

- parent `mm_state`: `awaiting_external`

This fits the current lifecycle model, which already includes `awaiting_external` for durable external waiting. If the dashboard later needs a dedicated `merge_automation` stage marker, the implementation MUST add it through the standard `MoonMind.UserWorkflow` search-attribute update path rather than assuming `mm_stage` already carries that value.

---

## 10. `MoonMind.MergeAutomation` Input and Output

### 10.1 Input

Jira-backed merge automation may include a `postMergeJira` block under
`mergeAutomationConfig`. When `MoonMind.UserWorkflow` starts merge automation from a
PR-publishing workflow execution with a canonical Jira issue key, it enables this block by
default so the issue is completed after verified merge success.

```json
{
  "jiraIssueKey": "MM-403",
  "mergeAutomationConfig": {
    "postMergeJira": {
      "enabled": true,
      "issueKey": null,
      "transitionId": null,
      "transitionName": null,
      "strategy": "done_category",
      "required": true,
      "fields": {}
    }
  }
}
```

The post-merge step is intentionally owned by `MoonMind.MergeAutomation`, not by
`pr-resolver`. The resolver reports the merge disposition; the workflow performs
Jira mutation only after that disposition is `merged` or `already_merged`.

### 10.2 Post-merge Jira completion

Post-merge Jira completion uses the trusted Jira integration activity boundary.
The workflow passes compact merge context to
`merge_automation.complete_post_merge_jira`; the activity fetches the issue,
fetches available transitions with field metadata, and applies one validated
transition when it can do so safely.

Target issue resolution is strict:

- explicit `postMergeJira.issueKey` wins when provided;
- otherwise the workflow uses the normalized merge automation `jiraIssueKey`;
- captured workflow origin or publish context keys may be used when present;
- PR metadata issue keys are only a strict exact-key fallback;
- fuzzy Jira summary search and multi-issue completion are not part of this
  behavior.

Transition selection is also strict:

- an explicit transition ID must be currently available;
- an explicit transition name must match exactly, case-insensitively;
- automatic selection succeeds only when exactly one available transition targets
  Jira's done status category;
- missing required transition fields block completion unless defaults are
  configured in `postMergeJira.fields`;
- an issue already in the done category is treated as successful no-op.

If required completion returns `blocked` or `failed`, merge automation does not
return terminal success. The failure is surfaced as a Jira-sourced blocker with
operator-visible reason text.

### 10.2.1 Already-implemented no-change completion

For Jira-oriented PR-publishing runs where PR output is optional, the run may
finish with no repository changes because the Jira issue is already implemented.
When the agent or structured publish output explicitly confirms that already
implemented outcome, `MoonMind.UserWorkflow` invokes the same trusted Jira completion
activity directly and requires the selected issue to reach a done-category
status before the run can finish successfully.

This path is intentionally not driven by fuzzy text search over arbitrary issue
keys. The run uses the canonical Jira issue key from workflow metadata or a single
validated Jira key from the Jira-backed instruction text. If the no-change result is
ambiguous, for example it only says there was no diff but does not confirm the
issue was already implemented, MoonMind does not mutate Jira and the run summary
must state that no confirmation was available.

### 10.3 Output

Merge automation summaries include compact `postMergeJira` evidence:

```json
{
  "status": "merged",
  "postMergeJira": {
    "status": "succeeded",
    "issueKey": "MM-403",
    "issueKeySource": "merge_automation",
    "transitionId": "41",
    "transitionName": "Done",
    "alreadyDone": false,
    "transitioned": true
  },
  "artifactRefs": {
    "postMergeJiraResolution": "artifact-id-resolution",
    "postMergeJiraTransition": "artifact-id-transition"
  }
}
```

The evidence is compact and sanitized. It must explain selected issue, selection
source, transition or no-op decision, completion status, and failure reason
without embedding raw Jira credentials or large Jira payloads in workflow
history.

The full worker-bound input also includes parent workflow identity, publish
context, and resolver launch template:

```json
{
  "parentWorkflowId": "mm:parent",
  "parentRunId": "temporal-run-id",
  "publishContextRef": "artifact://...",
  "mergeAutomationConfig": { "...": "..." },
  "resolverTemplate": {
    "repository": "owner/repo",
    "targetRuntime": "codex",
    "requiredCapabilities": ["git", "gh"],
    "runtime": { "mode": "codex", "model": "...", "effort": "..." }
  }
}
```

### 10.4 Terminal status summary

```json
{
  "status": "merged",
  "prNumber": 123,
  "prUrl": "https://github.com/owner/repo/pull/123",
  "cycles": 2,
  "resolverChildWorkflowIds": [
    "merge-auto-resolver:mm-parent:1",
    "merge-auto-resolver:mm-parent:2"
  ],
  "lastHeadSha": "abc123",
  "blockers": [],
  "postMergeJira": {
    "status": "succeeded",
    "issueKey": "MM-403",
    "transitioned": true
  }
}
```

Allowed terminal `status` values:

- `merged`
- `already_merged`
- `blocked`
- `failed`
- `expired`
- `canceled`

---

## 11. `MoonMind.MergeAutomation` Lifecycle

### 11.1 States

Use existing lifecycle vocabulary:

- `initializing`
- `awaiting_external` - gate waiting
- `executing` - resolver child run active
- `finalizing`
- `completed`
- `failed`
- `canceled`

No new `mm_state` is required for v1.

### 11.2 Durable loop

`MoonMind.MergeAutomation` runs the following loop:

1. load `PublishContext`
2. evaluate merge gate
3. if gate blocked:
   - wait for signal or fallback timer
   - continue
4. if gate open:
   - start resolver child `MoonMind.UserWorkflow`
   - await resolver result
5. inspect resolver result:
   - merged / already_merged -> success
   - reenter_gate -> return to step 2
   - manual_review / failed -> fail
6. finalize and return result to parent

---

## 12. Merge Gate Evaluation

The merge gate decides **when resolver is allowed to start**. It does not replace resolver logic.

### 12.1 Gate inputs

The gate evaluates at least:

- PR open/closed/merged state
- current PR head SHA
- whether required checks for the current head SHA have reported
- whether required checks are still running
- whether configured external review providers have completed for the current head SHA
- optional Jira issue status

Completed-but-failing checks do not keep the gate closed by themselves. Once
required checks have reported and are no longer running, the gate may launch
`pr-resolver` so resolver-owned CI remediation can proceed.

Detected merge conflicts are also resolver-actionable. They should open the
gate for `pr-resolver` instead of leaving merge automation in external wait.

### 12.2 Gate semantics

The gate opens when the configured external merge-readiness signal is complete for the **current head SHA**.

For GitHub checks, "complete" means the relevant check state has reported for
the current head SHA and is no longer running. A red check result is resolver
input, not a wait-only blocker.

That means the gate is **head-SHA-sensitive**. Any new push invalidates prior review/check completion for merge-automation purposes.

### 12.3 Callback-first, polling fallback

External waiting SHOULD be callback-first with bounded polling fallback, consistent with MoonMind's Temporal posture for external work.

`MoonMind.MergeAutomation` MUST support:

- external event signals from GitHub/Jira webhook handlers
- bounded timer-based re-evaluation fallback
- Continue-As-New for long-lived waits

### 12.4 Gate output contract

A gate evaluation returns:

```json
{
  "status": "waiting",
  "headSha": "abc123",
  "blockers": [
    { "kind": "review_provider_pending", "label": "AI review" },
    { "kind": "check_running", "label": "build-and-test" }
  ],
  "readyToLaunchResolver": false
}
```

---

## 13. Resolver Child Workflow Strategy

### 13.1 Resolver child type

When the gate opens, `MoonMind.MergeAutomation` starts a child **`MoonMind.UserWorkflow`** with a single-purpose payload for `pr-resolver`.

That child run MUST set:

- `task.tool = { type: "skill", name: "pr-resolver" }`
- top-level `initialParameters.publishMode = "auto"`

This is required because `pr-resolver` itself owns git push and merge behavior.

### 13.2 Resolver child payload

```json
{
  "workflowType": "MoonMind.UserWorkflow",
  "initialParameters": {
    "repository": "owner/repo",
    "targetRuntime": "codex",
    "requiredCapabilities": ["git", "gh"],
    "publishMode": "auto",
    "task": {
      "instructions": "Resolve and merge PR #123 for parent workflow mm:parent.",
      "tool": {
        "type": "skill",
        "name": "pr-resolver"
      },
      "inputs": {
        "repo": "owner/repo",
        "pr": "123",
        "mergeMethod": "squash"
      }
    }
  }
}
```

### 13.3 Resolver child result contract extension

`pr-resolver` SHOULD expose a machine-readable disposition specifically for merge automation:

```json
{
  "mergeAutomationDisposition": "reenter_gate"
}
```

Allowed values:

- `merged`
- `already_merged`
- `reenter_gate`
- `manual_review`
- `failed`

This avoids making the merge-automation child infer too much from low-level resolver reasons.

The terminal resolver result artifact, normally `var/pr_resolver/result.json`,
MUST include this field. A resolver run that is explicitly launched by
`MoonMind.MergeAutomation` but does not write a parseable resolver result is a
resolver failure, not a generic successful child run.

Resolver adapter boundaries MUST preserve continuation dispositions. When a
resolver artifact reports `mergeAutomationDisposition = "reenter_gate"`, or when
older artifacts encode the same intent through a continuation `next_step` such
as `run_fix_comments_skill`, `run_fix_ci_skill`,
`run_fix_merge_conflicts_skill`, `retry_finalize_after_backoff`, or
`wait_for_ci_and_retry_finalize`, the managed-agent adapter returns a successful
child result carrying `mergeAutomationDisposition = "reenter_gate"` only when
the resolver run carries the `mergeGate` owner injected by
`MoonMind.MergeAutomation`. It MUST NOT convert that gate-owned state into an
agent failure merely because the resolver process used a non-zero exit code such
as `attempts_exhausted`; merge automation owns the next gate cycle.

Long resolver waits also remain observable. While polling transient states such
as `ci_running`, resolver tooling SHOULD emit periodic progress output before
sleeping so managed-runtime stuck detection can distinguish healthy waiting from
a silent stalled agent.

### 13.4 Ungated resolver runs MUST NOT report continuation as success

A continuation disposition (`reenter_gate`) only has meaning when a
`MoonMind.MergeAutomation` gate owns the next cycle: re-enter `awaiting_external`,
poll CI on the new head, and finalize the merge. Merge automation marks the
resolver child it launches by injecting a `mergeGate` block into the resolver's
`initial_parameters`. `mergeGate.parentWorkflowId` MUST be the
`MoonMind.MergeAutomation` workflow ID that is the resolver child's actual
Temporal parent, not the root user workflow that launched merge automation.

When `pr-resolver` is instead submitted as a **standalone top-level
`MoonMind.UserWorkflow`** (no `mergeGate` owner), there is no gate to re-enter. A
run that ends with `mergeAutomationDisposition = "reenter_gate"` in that case has
**not** resolved the PR — CI/merge finalization never happens — so the parent
workflow MUST NOT report `status: success`. It MUST fail the run terminally with
an actionable summary directing the operator to re-submit under merge automation
or finalize manually. This prevents a false-green completion where the PR is left
open and unmerged. Managed-runtime adapters suppress ungated continuation
disposition metadata and surface the resolver's terminal blocker summary as a
normal failed/blocked PR-resolution result. Terminal dispositions (`merged`,
`already_merged`) and gated continuation runs are unaffected.

---

## 14. Post-Resolver Re-Gating

This design assumes resolver may push new commits.

A resolver-generated push can invalidate prior AI review/check signal completion. Because of that:

- the resolver child MUST NOT be the final authority on merge timing after it changes the head SHA,
- the merge-automation child MUST be able to re-enter the gate loop on the new head SHA.

The recommended pattern is:

1. resolver pushes changes,
2. resolver detects that external review signal is no longer complete,
3. resolver returns `mergeAutomationDisposition = "reenter_gate"`,
4. `MoonMind.MergeAutomation` re-enters `awaiting_external`,
5. once signal is re-established, it launches the next resolver child attempt.

---

## 15. Shared Gate Semantics Between Gate and Resolver

To avoid early merge after resolver-generated pushes, MoonMind SHOULD align the merge gate and `pr-resolver` on one shared semantic contract.

This does **not** require the same runtime process or exact same Python module. It does require the same logical contract:

- same head-SHA rules,
- same review-provider freshness rules,
- same required-check completeness rules,
- same blocker categories.

The merge gate and the resolver snapshot/finalize logic may use different implementations, but they MUST agree on contract semantics.

Before any resolver child has launched, `MoonMind.MergeAutomation` MAY adopt the
latest observed PR head SHA when a readiness evaluation sees that the published
head changed before the first resolver launch. This keeps normal post-publish
head updates from terminally failing the parent before the resolver has had a
chance to act, including when the updated head is already ready to merge. After
a resolver child has launched, revision changes MUST use the resolver
disposition and gate re-entry contract instead of silently advancing the tracked
head.

---

## 16. Dependency Semantics

For workflow executions with merge automation enabled:

- the root parent `workflowId` remains the only dependency target,
- downstream `dependsOn` relationships stay unchanged,
- the parent workflow does not complete successfully until merge automation succeeds.

This gives the operator the desired behavior: another workflow execution can depend on the original workflow and naturally wait for PR publish + gate + resolver completion without discovering a later-created top-level workflow.

---

## 17. Terminal Outcome Rules

### 17.1 Parent success

The parent `MoonMind.UserWorkflow` succeeds only when `MoonMind.MergeAutomation` returns:

- `merged`
- `already_merged`

### 17.2 Parent failure

The parent `MoonMind.UserWorkflow` fails when `MoonMind.MergeAutomation` returns:

- `blocked`
- `failed`
- `expired`

This is intentional. Under the current dependency model, only terminal success should satisfy downstream dependencies.

### 17.3 Future extension

A future system MAY introduce split completion concepts such as:

- `implementation_complete`
- `task_complete`

That is out of scope for v1 of this design.

---

## 18. Cancellation Semantics

- Canceling the parent workflow cancels `MoonMind.MergeAutomation`.
- Canceling `MoonMind.MergeAutomation` cancels any in-flight resolver child run.
- Child cleanup remains best-effort and truthful.

This follows MoonMind's existing child-workflow cancellation posture.

---

## 19. Continue-As-New

`MoonMind.MergeAutomation` is expected to be long-lived enough to require Continue-As-New support.

On Continue-As-New it MUST preserve:

- parent workflow id
- publish context ref
- current PR number / URL
- latest known head SHA
- configured gate policy
- Jira issue key
- active blockers
- cycle count
- resolver child attempt history
- expire-at deadline

This matches MoonMind's general Continue-As-New posture for long-lived workflows.

---

## 20. Visibility and Artifacts

### 20.1 Parent workflow detail

The parent workflow detail should show a **Merge Automation** panel with:

- status
- PR link
- current blockers
- latest head SHA
- current cycle
- resolver attempt history
- child workflow links

### 20.2 Child artifacts

`MoonMind.MergeAutomation` SHOULD write:

- `reports/merge_automation_summary.json`
- `artifacts/merge_automation/gate_snapshots/<cycle>.json`
- `artifacts/merge_automation/resolver_attempts/<attempt>.json`

### 20.3 Root terminal summary

The parent `reports/run_summary.json` SHOULD include:

```json
{
  "mergeAutomation": {
    "enabled": true,
    "status": "merged",
    "prNumber": 123,
    "prUrl": "...",
    "childWorkflowId": "merge-auto:mm-parent",
    "resolverChildWorkflowIds": ["..."],
    "cycles": 2
  }
}
```

---

## 21. UI Contract

The dashboard SHOULD expose this under PR publish settings as:

- `Publish mode: PR`
- `Automatically resolve/merge this PR`
- `Trigger when: external review signal is complete`
- optional Jira status gate
- optional review-provider configuration

This feature should not appear as a separate dependency or scheduling surface.

---

## 22. Rejected Alternatives

### 22.1 Fixed-delay follow-up workflow

Rejected because it is weaker than state-based gating and duplicates waiting logic already better expressed through durable workflow wait + external signals.

### 22.2 Separate top-level resolver workflow

Rejected because it creates a second dependency target and treats parent-owned subordinate work like an independent workflow relationship.

### 22.3 Directly execute resolver skill inside `MoonMind.MergeAutomation`

Rejected for v1 because it would duplicate existing `MoonMind.UserWorkflow` skill-execution substrate and bypass standard run-level artifacts, logs, and execution plumbing.

---

## 23. Acceptance Criteria

This design is complete when:

1. a PR-publishing parent workflow can enable merge automation,
2. the parent starts `MoonMind.MergeAutomation` as a child workflow after PR publish,
3. the parent does not complete until the merge-automation child completes,
4. `MoonMind.MergeAutomation` waits on external signal completion rather than a fixed delay,
5. `MoonMind.MergeAutomation` launches a child `MoonMind.UserWorkflow` for `pr-resolver`,
6. resolver child runs use `publishMode = "auto"`,
7. a resolver-generated push can return control to the gate,
8. downstream workflow executions depending on the parent workflow naturally wait for merge automation completion,
9. non-success merge-automation terminal outcomes fail the parent workflow except `canceled`, which cancels the parent workflow,
10. root and child artifacts expose enough state for the dashboard to explain why a workflow execution is waiting or failed.
