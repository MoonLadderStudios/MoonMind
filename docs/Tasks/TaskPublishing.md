# Task Publishing

Task publishing controls how agent-produced changes reach the repository after execution. The `publishMode` field on a task determines whether changes are committed only, pushed to a branch, or turned into a pull request.

## Publish Modes

| Mode     | Behavior |
|----------|----------|
| `none`   | No publishing. Changes remain in the agent's workspace only. |
| `branch` | Changes are committed and pushed to the selected branch on the remote. |
| `pr`     | Changes are committed, pushed to a work branch, and a pull request is created against the base branch. |

### `none`

The agent runs in its workspace but no git operations occur after completion. Useful for read-only tasks (analysis, diagnostics, research) or for tasks with side effects other than a final publish action.

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

When merge automation is explicitly enabled for a PR-publishing task, successful PR publication starts a parent-owned `MoonMind.MergeAutomation` child workflow. The original `MoonMind.Run` remains in `awaiting_external` while merge automation waits for configured external readiness signals and runs `pr-resolver` with publish mode `none`; downstream dependencies on the original task are satisfied only after merge automation succeeds.

For Jira-backed PR-publishing tasks, the authored or preset-provided Jira issue key must be preserved as the canonical `jiraIssueKey` in merge automation input. When that key is present, `MoonMind.Run` enables `mergeAutomationConfig.postMergeJira` by default so `MoonMind.MergeAutomation` can complete the same authoritative issue after verified merge success. If operators provide an explicit `postMergeJira.issueKey`, it overrides the canonical key for the post-merge completion step.

The publish path must not infer post-merge completion targets through fuzzy summary search or by transitioning every issue key found in PR metadata. PR metadata is only a strict fallback when stronger configured or captured Jira context is unavailable.

## Branch Naming

### Auto-generated Branches

When `publishMode` is `pr`, the runtime planner auto-generates a work/head branch name:

```
{clean-title-prefix}-{uuid8}
```

- **`clean-title-prefix`**: Derived from the task title (or parameter title, or skill name). Lowercased, non-alphanumeric characters replaced with `-`, truncated to 40 characters.
- **`uuid8`**: First 8 characters of a UUID4 for uniqueness.

**Examples:**
- Task title "Fix login page" → `fix-login-page-a1b2c3d4`
- No title available → `e5f6a7b8` (UUID only)

### Branch Fields

New authored submissions use one branch field consistently across UI, API, snapshots, and runtime planning:

| Field | Role | Description |
| --- | --- | --- |
| `branch` | Authored branch selection | For `publishMode: pr`, the selected repo branch and PR base. For `publishMode: branch`, the branch to update and push. |

`targetBranch` is not an authored or operator-facing field in new submissions. For PR mode, the head/work branch is runtime-generated or provider-managed and is not part of the create-form contract. `Publish Mode` remains part of the task contract; only its UI placement changed.

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

The publisher must not derive the pull request title or body from arbitrary task-step instructions, workflow control text, Jira transition instructions, or the first step in a multi-step orchestration. In particular, titles such as the following are invalid for implementation pull requests:

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

### Agent Instructions

Agents receive a commit-only instruction:

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

| Aspect | Managed Agents (Codex, Claude, Gemini CLI) | Jules |
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
