# Workflow Publishing

Workflow publishing controls how agent-produced changes reach the repository after execution. The `publishMode` field on a Workflow Execution, also represented in canonical payloads as `publish.mode`, determines whether publishing is disabled, handled by MoonMind infrastructure, or left to the agent under auto publish.

## Publish Modes

| Mode | Behavior |
| --- | --- |
| `auto` | Agent-owned publishing. The agent or selected skill determines whether to no-op, commit, push, update a PR branch, merge, or block, then writes publish evidence proving the result. |
| `none` | Publishing is disabled. No commit, push, pull request, or merge should happen. |
| `branch` | MoonMind-managed publishing commits and pushes changes to the selected branch on the remote. |
| `pr` | MoonMind-managed publishing commits changes, pushes a work branch, and creates a pull request against the base branch. |

### `auto`

`auto` is the user-facing mode for agent-owned publishing. It mirrors skill selection: when `skill.id = "auto"`, the agent determines which skill to use; when `publish.mode = "auto"`, the agent determines the correct publish action for the selected task and skill.

The canonical user payload stays simple:

```json
{
  "publish": {
    "mode": "auto"
  }
}
```

MoonMind expands that into an effective declarative contract:

```yaml
publish:
  mode: auto
  owner: agent
  requiredEvidence: true
  allowedActions:
    - no_op_verified
    - commit
    - push
    - merge
  failureBehavior: block_if_unverified
```

In auto mode, the agent may choose no-op, commit, push, PR-branch update, or merge when that is required by the selected skill. A run is successful only when the agent writes structured publish evidence proving the chosen outcome. MoonMind must not describe auto publish as disabled.

Auto-publish-capable skills include the PR-resolution and delegated publish skills that own their repository side effects, such as `pr-resolver`, `batch-pr-resolver`, `batch-dependabot-resolver`, `batch-workflows`, `fix-comments`, `fix-ci`, and `fix-merge-conflicts`. Long-term, the source of truth should be skill metadata, not only a hardcoded compatibility list. A skill can declare the contract explicitly:

```yaml
metadata:
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    verifyRemoteHead: exact
```

Normalization rules for auto-publish-capable skills:

- omitted publish mode resolves to `auto`.
- explicit `auto` resolves to `auto`.
- legacy explicit `none` resolves to `auto` with a deprecation diagnostic.
- `branch` and `pr` are rejected unless the skill explicitly opts into MoonMind-managed publishing.

For non-auto-publish-capable tasks, `auto` is invalid unless the selected skill or runtime contract declares that the agent owns publishing. This keeps `auto` from silently meaning the default PR publisher.

#### Auto publish evidence

Every `publish.mode = "auto"` run must write one shared evidence artifact:

```text
artifacts/publish_result.json
```

The artifact is bounded, non-secret, and uses this schema shape:

```json
{
  "schemaVersion": "moonmind.publish.auto.v1",
  "mode": "auto",
  "owner": "agent",
  "skillId": "fix-ci",
  "status": "verified",
  "action": "push",
  "repository": "MoonLadderStudios/MoonMind",
  "branch": "feature/example",
  "localHead": "abc123...",
  "remoteBranchHead": "abc123...",
  "remoteVerified": true,
  "pushed": true,
  "merged": false,
  "prUrl": "https://github.com/org/repo/pull/123",
  "blockedReason": null,
  "verificationCommands": [
    "git rev-parse HEAD",
    "git ls-remote origin refs/heads/feature/example"
  ]
}
```

Allowed `status` values:

- `verified`
- `no_op_verified`
- `blocked`
- `failed`

Allowed `action` values:

- `none`
- `commit`
- `push`
- `merge`
- `commit_and_push`
- `push_and_merge`

A blocked run should still write the artifact when possible:

```json
{
  "schemaVersion": "moonmind.publish.auto.v1",
  "mode": "auto",
  "owner": "agent",
  "skillId": "fix-comments",
  "status": "blocked",
  "action": "push",
  "repository": "MoonLadderStudios/MoonMind",
  "branch": "feature/example",
  "localHead": "abc123...",
  "remoteBranchHead": null,
  "remoteVerified": false,
  "pushed": false,
  "merged": false,
  "prUrl": "https://github.com/org/repo/pull/123",
  "blockedReason": "publish_unavailable"
}
```

#### Auto verification standard

Local success is not enough for auto publish. A successful auto-publish run must prove one of these outcomes:

1. The exact local `HEAD` is visible on the remote branch.
2. The pull request was merged.
3. No repository change was needed, and the current `HEAD` was already verified on the remote branch.

If push, merge, GitHub authentication, or remote-head verification is unavailable, the agent must block with `blockedReason = "publish_unavailable"` or a more precise reason. It must not report success based only on a local commit.

#### Auto finish outcomes

`PUBLISH_DISABLED` is reserved for true `publish.mode = "none"`. Auto publish must map from evidence, not from the absence of the MoonMind-managed publish stage.

| Auto evidence | Finish outcome |
| --- | --- |
| `status=verified`, `merged=true` | `PUBLISHED_PR` with `publish.mode=auto` and `publish.owner=agent` |
| `status=verified`, `pushed=true` | `PUBLISHED_BRANCH` with `publish.mode=auto` and `publish.owner=agent` |
| `status=no_op_verified` | `NO_COMMIT` with `publish.mode=auto` |
| `status=blocked`, `blockedReason=publish_unavailable` | blocked or failed at `finishOutcome.stage = "publish"` |
| missing evidence artifact | failed at `finishOutcome.stage = "publish"` with reason `auto_publish_evidence_missing` |

Preferred structured run-summary shape:

```json
{
  "finishOutcome": {
    "code": "PUBLISHED_PR",
    "stage": "publish",
    "reason": "Agent auto-published and verified the PR branch."
  },
  "publish": {
    "mode": "auto",
    "owner": "agent",
    "status": "verified",
    "action": "push",
    "branch": "feature/example",
    "localHead": "abc123",
    "remoteBranchHead": "abc123",
    "prUrl": "https://github.com/org/repo/pull/123",
    "evidenceRef": "artifact://..."
  }
}
```

UI copy should use `Publish: Auto` and render successful evidence as `Auto publish verified`. Blocked evidence should render as `Auto publish blocked: <reason>`. True `none` mode should render as `Publish disabled`.

### `none`

Publishing is disabled. The agent runs in its workspace, but no repository publish action should happen during or after execution. This is useful for read-only Workflow Executions such as analysis, diagnostics, and research, or for Workflow Executions whose only intended side effects are outside repository publication.

`none` must not be overloaded to mean agent-owned commit, push, or merge. Legacy payloads that used `none` for known auto-publish-capable skills should be normalized to `auto` for active execution and surfaced with a compatibility diagnostic.

### `branch`

After the agent completes, MoonMind infrastructure pushes the current work branch to the remote. The agent is instructed to commit but **not** to push or create a PR — that is handled deterministically by the runtime.

For new authored submissions, `branch` publish uses a single operator-selected `branch` field. That branch is the branch to update and push. MoonMind no longer exposes a separate "clone from X, push to Y" authoring model.

Example:

```json
{
  "publishMode": "branch",
  "git": {
    "branch": "156-jira-ui-runtime-config"
  }
}
```

### `pr`

For PR publication, the authored `branch` is the selected repository branch and PR base. MoonMind creates or obtains a runtime-generated work branch for the PR head, pushes changes there, and creates a pull request back to the authored base branch.

When merge automation is explicitly enabled for a PR-publishing Workflow Execution, successful PR publication starts a parent-owned `MoonMind.MergeAutomation` child workflow. The original `MoonMind.UserWorkflow` remains in `awaiting_external` while merge automation waits for configured external readiness signals and runs `pr-resolver` with publish mode `auto`; downstream dependencies on the original Workflow Execution are satisfied only after merge automation succeeds.

Operator-facing detail payloads present PR publishing with merge automation as the single publish mode value `pr_with_merge_automation`. Worker-bound execution input remains normalized as `publishMode = "pr"` plus merge automation configuration, because merge automation is an orchestration extension of PR publishing rather than a separate repository publish primitive. Details must not expose a second selection flag such as `mergeAutomationSelected`; active or terminal merge automation state belongs under the `mergeAutomation` status object.

For Jira-backed PR-publishing Workflow Executions, the authored or preset-provided Jira issue key must be preserved as the canonical `jiraIssueKey` in merge automation input. When that key is present, `MoonMind.UserWorkflow` enables `mergeAutomationConfig.postMergeJira` by default so `MoonMind.MergeAutomation` can complete the same authoritative issue after verified merge success. If operators provide an explicit `postMergeJira.issueKey`, it overrides the canonical key for the post-merge completion step.

The publish path must not infer post-merge completion targets through fuzzy summary search or by transitioning every issue key found in PR metadata. PR metadata is only a strict fallback when stronger configured or captured Jira context is unavailable.

If a Jira-oriented PR-publishing Workflow Execution completes with no repository changes because the issue is already implemented, `MoonMind.UserWorkflow` completes that authoritative Jira issue through the same trusted Jira transition boundary used for post-merge completion. Ambiguous no-change results that do not explicitly confirm the issue is already implemented remain non-mutating and must say so in the run summary.

## Branch Naming

### Auto-generated Branches

When `publishMode` is `pr`, the runtime planner auto-generates a work/head branch name:

```
{clean-title-prefix}-{uuid8}
```

- **`clean-title-prefix`**: Derived from the Workflow Execution title (or parameter title, or skill name). Lowercased, non-alphanumeric characters replaced with `-`, truncated to 40 characters.
- **`uuid8`**: First 8 characters of a UUID4 for uniqueness.

**Examples:**
- Workflow Execution title "Fix login page" → `fix-login-page-a1b2c3d4`
- No title available → `e5f6a7b8` (UUID only)

### Branch Fields

New authored submissions use one branch field consistently across UI, API, snapshots, and runtime planning:

| Field | Role | Description |
| --- | --- | --- |
| `branch` | Authored branch selection | For `publishMode: pr`, the selected repo branch and PR base. For `publishMode: branch`, the branch to update and push. For `publishMode: auto`, the agent-owned publisher may use it as the current PR branch or PR selector. |

`targetBranch` is not an authored or operator-facing field in new submissions. For PR mode, the head/work branch is runtime-generated or provider-managed and is not part of the create-form contract. `Publish Mode` remains part of the Workflow Execution contract; only its UI placement changed.

### Legacy Migration

Older snapshots and execution payloads may still contain `startingBranch` and `targetBranch`.

Rules:

- New authored submissions must emit `git.branch` only.
- `startingBranch` may be normalized to the new authored `branch` when reconstructing older submissions.
- Legacy `targetBranch` may be retained only as historical metadata for audit/debug displays.
- Legacy `targetBranch` must never drive active new submission logic.
- Legacy two-branch branch-publish snapshots whose old intent cannot be represented by one authored `branch` must surface a reconstruction warning instead of pretending to round-trip exactly.

## Branch Resolution

Runtime planning starts from the authored `branch`.

### Authored Branch

Resolved via this fallback chain:

1. `task.git.branch`
2. `task.branch`
3. Selected skill inputs
4. Parameter payload
5. Input payload
6. Repository default branch

For `publishMode: pr`, the authored branch is the PR base branch. The PR head branch is resolved separately from runtime-managed work-branch state, such as workspace metadata, adapter result metadata, or provider PR metadata.

For `publishMode: branch`, the authored branch is the branch the infrastructure updates and pushes.

For `publishMode: auto`, the authored branch is input to the agent-owned publish contract. A PR-resolving skill may treat it as the current PR branch or selector, while an implementation skill may use it as the branch whose remote head must be verified.

### Work Branch (PR source)

When the workflow creates a PR, it resolves the work/head branch from runtime-owned sources:

1. Provider or agent execution outputs such as `outputs.branch`
2. Workspace work-branch metadata
3. Runtime planner generated branch name

If no PR head branch can be resolved for `publishMode: pr`, the workflow raises an error.

## Post-Agent Git Push

After a managed agent subprocess completes successfully in MoonMind-managed `branch` or `pr` mode, the infrastructure performs a deterministic `git push` of the work branch. This is **not** delegated to the agent via prompt instructions — it is an infrastructure guarantee.

In `auto` mode, the post-agent infrastructure push is not the publish owner. The agent or selected skill owns commit/push/merge decisions and must write the auto publish evidence artifact. MoonMind finalization consumes that evidence instead of running the generic managed publish stage.

GitHub publishing resolves credentials through the canonical GitHub resolver before the push. The push command receives `GITHUB_TOKEN`, `GH_TOKEN`, and `GIT_TERMINAL_PROMPT=0` in its subprocess environment when a token is available, so managed publishing does not depend on machine-level git credential caches.

### Safety Guard

Before pushing in MoonMind-managed modes, the runtime resolves the current branch name (`git rev-parse --abbrev-ref HEAD`) and **refuses to push** if the branch is:

- `main`
- `master`
- `HEAD` / detached or unknown branch state

For `publishMode: pr`, the authored base branch remains protected because the workflow pushes a separate runtime-owned head branch and creates a PR back to the base.

For `publishMode: branch`, the authored `branch` is publishable only when it is not one of the hard-protected names above.

If the branch is protected, the push is skipped with a warning log. This prevents accidental pushes to production branches if the agent switched branches or if branch creation failed.

## Pull Request Creation

For GitHub repositories, managed PR publishing uses the GitHub REST API when the runtime has a resolved repository credential. This keeps fine-grained PAT permission failures tied to the exact endpoint and exposes GitHub diagnostics such as the response message, documentation URL, and accepted-permissions header.

If no repository-scoped token is available, the runtime may fall back to `gh pr create` with explicit `GH_TOKEN` / `GITHUB_TOKEN` environment injection. Ambient `gh auth` state is not a reliable managed-runtime contract.

The minimum fine-grained PAT permissions for PR publishing are:

- `Contents: Read and write`
- `Pull requests: Read and write`
- `Workflows: Write` only when changes include `.github/workflows/*`

Readiness evaluation additionally needs:

- `Commit statuses: Read`
- `Checks: Read`
- `Issues: Read` when reaction fallback is enabled

### Pull Request Title and Body Metadata

Pull request titles and bodies are semantic review artifacts. They must describe the implemented change, not the orchestration mechanics that produced the change.

MoonMind publishing owns the durable side-effect boundary for managed `branch` and `pr` modes: selecting the repository and base branch, resolving credentials, pushing the work branch, creating or updating the pull request, recording the confirmed pull request URL, and enforcing downstream gates such as Jira Code Review transitions. Agents own the semantic description of their work: summary, rationale, test evidence, remaining risks, and reviewer-facing pull request metadata.

In `auto` mode, the agent or selected skill owns the durable repository side effect as well. It still must keep semantic metadata separate from workflow control text and record the confirmed URL, branch, commit, merge, and verification evidence in `artifacts/publish_result.json`.

Managed runtimes should therefore treat pull request metadata as a structured work product produced by the agent and consumed by the publisher. The preferred contract is:

1. The agent proposes a concise pull request title and body after it has seen the final diff and verification evidence.
2. MoonMind validates the proposed metadata against simple invariants.
3. MoonMind creates or updates the pull request through the managed publishing path, or the auto-publish skill records the resulting PR URL in publish evidence.
4. Downstream workflows consume the confirmed pull request URL and validated metadata, not free-form log output.

For Jira-backed work, the pull request title must include the canonical Jira issue key and should normally use the format:

```text
<ISSUE-KEY> <implemented capability>
```

Examples:

```text
MM-597 Validate proposal delivery records before submission
MM-489 Render shimmer band and halo layers
MM-398 Add Jira Orchestrate blocker preflight
```

The pull request body should include, when available:

- Jira issue key and link
- Active MoonSpec feature path
- Summary of implemented behavior
- Verification verdict
- Tests run
- Remaining risks or follow-up work

The publisher must not derive the pull request title or body from arbitrary Workflow Execution step instructions, workflow control text, Jira transition instructions, or the first step in a multi-step orchestration. In particular, titles such as the following are invalid for implementation pull requests:

```text
Change Jira issue MM-597 to status In Progress before implementation starts.
Move Jira issue MM-597 to Code Review.
Use the trusted Jira issue updater workflow.
```

Those strings describe control-plane actions, not the implemented code change.

Higher-level presets may require a pull request URL before they can complete a trusted side effect. For example, Jira Orchestrate needs a confirmed pull request URL before moving the issue to Code Review. In those cases, the preset should still separate responsibilities:

- Jira transition and blocker-check steps run with no repository publishing.
- Implementation and verification steps produce the code changes and evidence.
- The pull-request handoff step publishes only after the implementation is complete.
- The handoff step uses validated pull request metadata derived from the final implementation, not from earlier Jira status-transition steps.
- The workflow records the confirmed pull request URL before any downstream Jira transition depends on it.

Provider-native publishing may supply pull request metadata directly when the provider has a reliable native contract. MoonMind should still capture the resulting pull request URL, readiness state, branch information, and metadata in the run record so retries, audit, merge automation, and Jira transitions remain deterministic.

### MoonSpec Verification Gate

Workflows that include MoonSpec verification gates must use the latest structured verification verdict to decide publication eligibility.

`FULLY_IMPLEMENTED` permits PR publication and downstream trusted side effects. `ADDITIONAL_WORK_NEEDED` keeps the workflow in the bounded remediation loop while a later MoonSpec remediation step remains. Once that retry budget is exhausted, `ADDITIONAL_WORK_NEEDED` blocks publication.

Non-retryable blocking verdicts, including `NO_DETERMINATION`, `BLOCKED`, and `FAILED_UNRECOVERABLE`, block publication without waiting for additional remediation attempts unless the workflow explicitly models the missing evidence as recoverable work inside the same bounded plan.

When MoonSpec verification blocks publication, the workflow records `publicationBlockedBy: "moonspec_verify"` in publish context, preserves the latest verification report and evidence refs, writes a compact `failureSummary.type = "moonspec_verification_gate"` block in `reports/run_summary.json`, and marks downstream publication or Jira handoff steps skipped rather than creating a pull request with known incomplete work.

### Agent Instructions

For MoonMind-managed `branch` and `pr` modes, agents receive a commit-only instruction:

> After completing the changes above, commit your work (`git add -A && git commit -m '<summary>'`). Do NOT push or create a pull request — that is handled automatically.

For `none`, agents receive a publish-disabled instruction:

> Do NOT commit or push. Publishing is disabled for this task.

For `auto`, agents receive an agent-owned publish instruction:

> Publishing is in auto mode. Determine the correct publish action for this task. You may commit, push, or merge only when required by the selected skill. Write `artifacts/publish_result.json` proving the outcome before reporting success.

Some higher-level presets may include an explicit pull-request handoff step because a later trusted side effect needs the PR URL before workflow finalization (for example, Jira Orchestrate moving an issue to Code Review). In those cases, the step-specific handoff instruction is the controlling instruction for that step, but the handoff must still use validated pull request metadata derived from the completed implementation and verification evidence. It must not use earlier control-plane step text, such as Jira status-transition instructions, as the pull request title or body. The resulting PR URL must be recorded for the workflow to consume.

## Jules: Special Case

Jules is an external agent provider with its own PR creation mechanism. Unlike managed CLI agents, Jules handles publishing through its API rather than git commands.

### How Jules Publishing Differs

| Aspect | Managed Agents (Codex, Claude, Gemini CLI) | Jules |
|--------|---------------------------------------------|-------|
| Branch creation | Launcher creates head branch locally | Jules API manages branches internally |
| Commit & push | Infrastructure pushes after agent completes | Jules handles internally |
| PR creation | Workflow calls `repo.create_pr` activity | Jules API uses `automationMode: AUTO_CREATE_PR` |
| Prompt suffix | Commit-only instruction appended | No instruction appended |

### `automationMode`

When `publishMode` is `pr` or `branch`, the Jules adapter sets `automationMode: AUTO_CREATE_PR` on the session creation request. This tells the Jules API to automatically create a PR when the agent finishes.

`publishMode = "auto"` is valid for Jules only when the adapter declares provider-native auto publish support and returns equivalent publish evidence. Otherwise, auto mode should block before launch rather than silently falling back to managed PR publication.

The Jules adapter extracts the resulting PR URL from:
1. The `pull_request_url` field on the Jules task response
2. The diagnostics artifact (via regex pattern matching for GitHub PR URLs)

### Jules Runtime Exclusion

The runtime planner maintains a set of runtimes that handle PR creation natively:

```python
_TOOLS_WITH_AUTO_PR_CREATION = frozenset({"jules", "jules_api"})
```

For these runtimes, the commit/push instruction suffix is **not** appended to the agent's prompt, and the infrastructure-level git push is **not** performed (Jules runs externally, not in a local workspace).
