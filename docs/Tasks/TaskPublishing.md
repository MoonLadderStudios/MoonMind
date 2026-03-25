# Task Publishing

Task publishing controls how agent-produced changes reach the repository after execution. The `publishMode` field on a task determines whether changes are committed only, pushed to a branch, or turned into a pull request.

## Publish Modes

| Mode     | Behavior |
|----------|----------|
| `none`   | No publishing. Changes remain in the agent's workspace only. |
| `branch` | Changes are committed and pushed to a work branch on the remote. |
| `pr`     | Changes are committed, pushed to a work branch, and a pull request is created against the base branch. |

### `none`

The agent runs in its workspace but no git operations occur after completion. Useful for read-only tasks (analysis, diagnostics, research) or for tasks with side effects other than a final publish action.

### `branch`

After the agent completes, the infrastructure pushes the current work branch to the remote. The agent is instructed to commit but **not** to push or create a PR — that is handled deterministically by the runtime.

### `pr`

Same as `branch`, but after the push completes the workflow creates a pull request via the `repo.create_pr` activity. The PR merges the head branch into the base branch (see [Branch Resolution](#branch-resolution) below).

## Branch Naming

### Auto-generated Branches

When `publishMode` is `pr` and no explicit head branch is provided, the runtime planner auto-generates a branch name:

```
{clean-title-prefix}-{uuid8}
```

- **`clean-title-prefix`**: Derived from the task title (or parameter title, or skill name). Lowercased, non-alphanumeric characters replaced with `-`, truncated to 40 characters.
- **`uuid8`**: First 8 characters of a UUID4 for uniqueness.

**Examples:**
- Task title "Fix login page" → `fix-login-page-a1b2c3d4`
- No title available → `e5f6a7b8` (UUID only)

### Branch Fields

The two primary branch fields used consistently across UI, API, and internal logic:

| Field            | Role | Description |
|------------------|------|-------------|
| `startingBranch` | Base | The branch to clone from. Also used as the PR base (merge destination). |
| `targetBranch`   | Head | The name for the agent's work branch. Becomes the PR head (source of changes). |

Additional fallback field:

| Field            | Role | Description |
|------------------|------|-------------|
| `branch`         | Fallback | General-purpose fallback for either head or base when the specific field is absent. |

The runtime planner resolves these from multiple sources in priority order:

1. `git` payload (`task.git.startingBranch`, `task.git.targetBranch`)
2. Task payload (`task.startingBranch`, etc.)
3. Selected skill inputs
4. Parameter payload
5. Input payload

## Branch Resolution

When the workflow creates a PR (`publishMode: pr`), it resolves the **head branch** (where changes live) and the **base branch** (where the PR merges into).

### Head Branch (PR source)

Resolved via this fallback chain:

1. Agent execution outputs: `outputs.branch` → `outputs.targetBranch`
2. Workspace spec: `targetBranch` → `branch`
3. Last plan node inputs: `targetBranch` → `branch`

If no head branch can be resolved, the workflow raises an error.

### Base Branch (PR destination)

Resolved via this fallback chain:

1. `workspaceSpec.startingBranch`
2. Last plan node inputs: `startingBranch`
3. Default: `main`

## Post-Agent Git Push

After a managed agent subprocess completes successfully, the infrastructure performs a deterministic `git push` of the work branch. This is **not** delegated to the agent via prompt instructions — it is an infrastructure guarantee.

### Safety Guard

Before pushing, the runtime resolves the current branch name (`git rev-parse --abbrev-ref HEAD`) and **refuses to push** if the branch is:

- `main`
- `master`
- The configured base branch (`startingBranch`)

If the branch is protected, the push is skipped with a warning log. This prevents accidental pushes to production branches if the agent switched branches or if branch creation failed.

### Agent Instructions

Agents receive a commit-only instruction:

> After completing the changes above, commit your work (`git add -A && git commit -m '<summary>'`). Do NOT push or create a pull request — that is handled automatically.

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
