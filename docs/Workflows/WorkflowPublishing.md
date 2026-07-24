# Workflow Publishing

Workflow publishing controls how agent-produced changes reach the repository after execution. The `publishMode` field on a Workflow Execution determines whether publishing is disabled, owned by an auto-publish-capable agent skill, committed for MoonMind-managed branch publishing, or committed for MoonMind-managed pull-request publishing.

## Publish Modes

| Mode     | Behavior |
|----------|----------|
| `auto`   | Agent-owned publishing. The selected skill decides whether to no-op, commit, push, merge, or block, and must write `artifacts/publish_result.json` evidence. |
| `none`   | No publishing. Changes remain in the agent's workspace only. |
| `branch` | Changes are committed and pushed to the selected branch on the remote. |
| `pr`     | Changes are committed, pushed to a work branch, and a pull request is created against the base branch. |

### `auto`

Auto mode is the canonical mode for skills that own repository side effects inside the managed runtime. Repository auto-publish capability is declared by skill metadata:

```yaml
metadata:
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
```

The built-in repository auto-publish skills are `pr-resolver`, `fix-comments`, `fix-ci`, and `fix-merge-conflicts`. The backend may retain a narrow migration fallback when metadata is temporarily unavailable, but catalog metadata is authoritative when present.

Publishing does not choose an implementation host. In particular,
`publish.mode = auto` grants the selected skill agent-owned publishing authority
and requires evidence; it does not select or authorize a native semantic
implementation. `pr-resolver` always executes its resolved Skill bundle through
the ordinary agent runtime path.

Resolution rules:

- Omitted publish mode resolves to `auto` for auto-publish-capable skills.
- Explicit `auto` resolves to `auto` for auto-publish-capable skills.
- Legacy explicit `none` for a known auto-publish-capable skill resolves to `auto` with a compatibility diagnostic.
- `branch` and `pr` are invalid for auto-publish-capable skills unless that skill explicitly opts into MoonMind-managed publishing.
- `auto` is invalid for tasks that do not declare agent-owned publishing capability.

Every successful auto run must produce `artifacts/publish_result.json` with `schemaVersion = "moonmind.publish.auto.v1"`, `mode = "auto"`, `owner = "agent"`, the selected skill id, status, action, repository, branch, local and remote head fields, remote verification status, push/merge booleans, optional PR URL, optional blocked reason, and verification commands.

Built-in auto-publish skills produce this evidence through the portable helper:

```bash
python3 .agents/skills/_shared/publish_evidence.py write-pushed \
  --skill-id <skill> \
  --repo <owner/repo> \
  --branch <branch>
```

The helper also supports `write-merged`, `write-no-op`, `write-blocked`, `write-failed`, and `from-pr-resolver-result`. It has no Temporal, database, or service-layer imports and can run in a local checkout outside MoonMind.

Allowed status values are `verified`, `no_op_verified`, `blocked`, and `failed`. Allowed action values are `none`, `commit`, `push`, `merge`, `commit_and_push`, and `push_and_merge`.

Successful auto evidence must prove one of:

- exact local `HEAD` is visible on the remote branch;
- the pull request was merged;
- no repository change was needed and local `HEAD` was verified against the remote branch.

Finish outcome mapping is evidence-driven:

- verified merge -> `PUBLISHED_PR` with `publish.mode = auto` and `publish.owner = agent`;
- verified push -> `PUBLISHED_BRANCH` with `publish.mode = auto` and `publish.owner = agent`;
- verified no-op -> `NO_COMMIT`, not `PUBLISH_DISABLED`;
- blocked or failed evidence -> publish-stage failure/block;
- missing evidence -> publish-stage failure with `auto_publish_evidence_missing`.

Native resolver terminal publication returns artifact references at the child
result boundary. The parent must load `publishEvidence` from either the direct
result field or `outputRefs` before determining its finish outcome. A terminal
projection row that has not yet been created is auxiliary lag: Temporal history
and terminal artifacts remain authoritative, and projection lag must not replace
a verified merge with a finalization failure.

### Non-Repository Side-Effect Outcomes

Some skills perform parent-level side effects without publishing repository changes. `batch-pr-resolver`, `batch-dependabot-resolver`, and `batch-workflows` queue child workflows and write summary artifacts such as `batch_pr_resolver_result.json`, `batch_dependabot_resolver_result.json`, `batch-workflows-result.json`, and `skill_outcome.json`. These parent skills resolve to `publish.mode = "none"` and must not be forced to produce repository remote-head evidence. Their child workflows keep their own publish mode, including `auto` for queued `pr-resolver` children.

### `none`

The agent runs in its workspace but no git operations occur after completion. Useful for read-only Workflow Executions (analysis, diagnostics, research) or for Workflow Executions with side effects other than a final publish action.

`none` is reserved for true publish-disabled behavior. `PUBLISH_DISABLED` finish summaries are valid only for resolved `publish.mode = "none"`.

### `branch`

After the agent completes, the infrastructure pushes the current work branch to the remote. The agent is instructed to commit but **not** to push or create a PR — that is handled deterministically by the runtime.

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

If a Jira-oriented PR-publishing Workflow Execution completes with no repository changes
because the issue is already implemented, `MoonMind.UserWorkflow` completes that
authoritative Jira issue through the same trusted Jira transition boundary used
for post-merge completion. Ambiguous no-change results that do not explicitly
confirm the issue is already implemented remain non-mutating and must say so in
the run summary.

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
| `branch` | Authored branch selection | For `publishMode: pr`, the selected repo branch and PR base. For `publishMode: branch`, the branch to update and push. |

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

### Work Branch (PR source)

When the workflow creates a PR, it resolves the work/head branch from runtime-owned sources:

1. Provider or agent execution outputs such as `outputs.branch`
2. Workspace work-branch metadata
3. Runtime planner generated branch name

If no PR head branch can be resolved for `publishMode: pr`, the workflow raises an error.

## Post-Agent Git Push

After a managed agent subprocess completes successfully, the infrastructure performs a deterministic `git push` of the work branch. This is **not** delegated to the agent via prompt instructions — it is an infrastructure guarantee.

GitHub publishing resolves credentials through the canonical GitHub resolver
before the push. The push command receives `GITHUB_TOKEN`, `GH_TOKEN`, and
`GIT_TERMINAL_PROMPT=0` in its subprocess environment when a token is available,
so managed publishing does not depend on machine-level git credential caches.

The managed push activity is the authority for whether a publishable repository
candidate exists. After a successful push (including an already-current remote
branch), it emits `acceptedRepositoryEvidence` with the work branch, base branch,
head commit, commits-ahead count, authorization/contamination disposition, and
remote-verification result. Verification Steps and terminal workflow gates
consume that typed envelope instead of reconstructing publication feasibility
from raw `push_*` metadata or agent prose. If the activity cannot produce a
complete, internally consistent envelope, draft and ready-for-review publication
remain blocked with an explicit artifact-backed handoff.

Before measuring commits, the activity refreshes the exact `origin/<base>`
tracking ref used as the publication base. After pushing, it queries the live
remote branch and requires its head to equal the local candidate head. A base
refresh failure, remote-head mismatch, or indeterminate commits-ahead count
downgrades the raw push result to a failed publication result; it never emits
accepted evidence or permits native PR creation from that incomplete state.

### Safety Guard

Before pushing, the runtime resolves the current branch name (`git rev-parse --abbrev-ref HEAD`) and **refuses to push** if the branch is:

- `main`
- `master`
- `HEAD` / detached or unknown branch state

For `publishMode: pr`, the authored base branch remains protected because the workflow pushes a separate runtime-owned head branch and creates a PR back to the base.

For `publishMode: branch`, the authored `branch` is publishable only when it is not one of the hard-protected names above.

If the branch is protected, the push is skipped with a warning log. This prevents accidental pushes to production branches if the agent switched branches or if branch creation failed.

## Pull Request Creation

For GitHub repositories, managed PR publishing uses the GitHub REST API when the
runtime has a resolved repository credential. This keeps fine-grained PAT
permission failures tied to the exact endpoint and exposes GitHub diagnostics
such as the response message, documentation URL, and accepted-permissions
header.

If no repository-scoped token is available, the runtime may fall back to
`gh pr create` with explicit `GH_TOKEN` / `GITHUB_TOKEN` environment injection.
Ambient `gh auth` state is not a reliable managed-runtime contract.

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

MoonMind publishing owns the durable side-effect boundary: selecting the repository and base branch, resolving credentials, pushing the work branch, creating or updating the pull request, recording the confirmed pull request URL, and enforcing downstream gates such as Jira Code Review transitions. Agents own the semantic description of their work: summary, rationale, test evidence, remaining risks, and reviewer-facing pull request metadata.

Managed runtimes should therefore treat pull request metadata as a structured work product produced by the agent and consumed by the publisher. The preferred contract is:

1. The agent proposes a concise pull request title and body after it has seen the final diff and verification evidence.
2. MoonMind validates the proposed metadata against simple invariants.
3. MoonMind creates or updates the pull request through the managed publishing path.
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

`FULLY_IMPLEMENTED` permits PR publication and downstream trusted side effects. `ADDITIONAL_WORK_NEEDED` keeps the workflow in the bounded remediation loop while a later MoonSpec remediation step remains. Once that retry budget is exhausted, a workflow whose publish mode is `pr` opens a draft pull request annotated with the remaining-work verdict and verification report, then fails with `attention_required: true` and skips downstream promotion or trusted handoff steps. The draft is recoverability evidence, not a successful terminal outcome. A pushed branch or created draft pull request must never be classified as `no_commit`.

Non-retryable blocking verdicts, including `NO_DETERMINATION`, `BLOCKED`, and `FAILED_UNRECOVERABLE`, block publication without waiting for additional remediation attempts unless the workflow explicitly models the missing evidence as recoverable work inside the same bounded plan.

The gate distinguishes a verifier judgment from a malformed verdict envelope:

- The runtime injects the canonical output contract (allowed `verdict` and
  `recommendedNextAction` values) into moonspec-verify instructions, and the
  artifact-publication boundary derives the canonical `recommendedNextAction`
  from the verifier `verdict`. When the model-authored action drifts from the
  canonical vocabulary or encodes a step-specific destination, MoonMind
  preserves the raw action as diagnostic evidence but validates the derived
  action.
- A gate payload that fails contract validation is downgraded fail-closed to
  `NO_DETERMINATION`, and the gate records a `downgradeReason` naming the
  declared verdict and the violating field in publish context, the blocking
  operator message, and the failure summary.
- Before treating a contract-violating verify output as blocking, the workflow
  re-runs the verify step a bounded number of times with corrective feedback so
  the verifier can rewrite its structured JSON. Remediation implement cycles
  are never spent on a malformed verdict envelope.

When MoonSpec verification blocks publication without a permitted draft handoff, the workflow records
`publicationBlockedBy: "moonspec_verify"` in publish context, preserves the
latest verification report and evidence refs, writes a compact
`failureSummary.type = "moonspec_verification_gate"` block in
`reports/run_summary.json`, and marks downstream publication or Jira handoff
steps skipped rather than creating a pull request with known incomplete work.

Operators may opt environment-class gate outcomes into draft publication with
`workflow.moonspec_environment_blocked_publish_action`
(`WORKFLOW_MOONSPEC_ENVIRONMENT_BLOCKED_PUBLISH_ACTION`, default `fail`). With
`draft_pr`, a gate stop whose outcome is environment-class — verdict `BLOCKED`,
or `NO_DETERMINATION` produced by a degraded/malformed gate payload — publishes
a **draft** pull request annotated with a "MoonSpec verification incomplete"
section and the verification report ref, and the run completes with
`attention_required: true` and a distinct summary instead of failing.
`FAILED_UNRECOVERABLE` and verifier-declared `NO_DETERMINATION` remain
fail-closed. `ADDITIONAL_WORK_NEEDED` uses the automatic draft handoff described
above only after the bounded remediation budget is exhausted.

### Agent Instructions

Agents receive instructions based on the resolved publish mode.

For `auto`:

> Publishing is in auto mode. Determine the correct publish action for this task. You may commit, push, or merge only when required by the selected skill. Write artifacts/publish_result.json proving the outcome before reporting success.

For `none`:

> Do NOT commit or push. Publishing is disabled for this task.

For MoonMind-managed `branch` and `pr`, agents receive a commit-only instruction:

> After completing the changes above, commit your work (`git add -A && git commit -m '<summary>'`). Do NOT push or create a pull request — that is handled automatically.

Some higher-level presets may include an explicit pull-request handoff step
because a later trusted side effect needs the PR URL before workflow finalization
(for example, Jira Orchestrate moving an issue to Code Review). In those cases,
the step-specific handoff instruction is the controlling instruction for that
step, but the handoff must still use validated pull request metadata derived
from the completed implementation and verification evidence. It must not use
earlier control-plane step text, such as Jira status-transition instructions, as
the pull request title or body. The resulting PR URL must be recorded for the
workflow to consume.

## Jules: Special Case

Jules is an external agent provider with its own PR creation mechanism. Unlike managed CLI agents, Jules handles publishing through its API rather than git commands.

### How Jules Publishing Differs

| Aspect | Managed Agents (Codex, Claude) | Jules |
|--------|---------------------------------------------|-------|
| Branch creation | Launcher creates head branch locally | Jules API manages branches internally |
| Commit & push | Infrastructure pushes after agent completes | Jules handles internally |
| PR creation | Workflow calls `repo.create_pr` activity | Jules API uses `automationMode: AUTO_CREATE_PR` |
| Prompt suffix | Commit-only instruction appended | No instruction appended |

### `automationMode`

When `publishMode` is `pr` or `branch`, the Jules adapter sets `automationMode: AUTO_CREATE_PR` on the session creation request. This tells the Jules API to automatically create a PR when the agent finishes.

The Jules adapter extracts the resulting PR URL from:
1. The `pull_request_url` field on the Jules task response
2. The diagnostics artifact (via regex pattern matching for GitHub PR URLs)

### Jules Runtime Exclusion

The runtime planner maintains a set of runtimes that handle PR creation natively:

```python
_TOOLS_WITH_AUTO_PR_CREATION = frozenset({"jules", "jules_api"})
```

For these runtimes, the commit/push instruction suffix is **not** appended to the agent's prompt, and the infrastructure-level git push is **not** performed (Jules runs externally, not in a local workspace).
