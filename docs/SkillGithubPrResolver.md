# Agent Skill: Github PR Resolver Technical Design

Status: Draft
Owners: MoonMind Engineering
Last Updated: 2026-02-23

## 1. Purpose

Add a **PR Resolver** skill that can be invoked from the Tasks Dashboard (or CLI) to:

1. **Resolve target PR** (defaults to the PR associated with the current branch).
2. **Fetch PR metadata + CI status + comments**.
3. **Diagnose and Delegate**: Use the "Read-and-Execute" (Instruction Composition) pattern to dynamically load and execute specialized sub-skills (e.g., `fix-merge-conflicts`, `fix-ci`) based on the PR state.
4. **Merge the PR** if everything is already good **and** no CI is currently running.

This skill acts as an **Umbrella Skill**. It relies on existing specialized skills to do the heavy lifting, ensuring robust, context-aware execution within a single CLI agent session.

---

## 2. Assumptions and Constraints

* Workers already support run-scoped skills via `.agents/skills` and `.gemini/skills` symlinks to a single active set.
* The worker environment has GitHub auth available via `GITHUB_TOKEN` for private repo operations and includes `gh` usage in existing publish flows.
* Specialized sub-skills (like `fix-merge-conflicts`) are available in the `.agents/skills/` directory.
* Because the PR Resolver performs git/PR mutations itself (via sub-skills), **tasks using this skill must set `publish.mode=none`** to avoid the system publish stage attempting a second publish/PR create.

---

## 3. Skill Packaging

### 3.1 Location

Add a new skill in the shared repo skill mirror:

```
.agents/skills/pr-resolver/
  SKILL.md
  bin/
    pr_resolve_snapshot.py
  schemas/
    pr_resolver_snapshot.schema.json
    pr_resolver_result.schema.json
```

Because we use the "Read-and-Execute" pattern, we do not need complex execution scripts here. The `pr-resolver` relies on its own `bin/pr_resolve_snapshot.py` for diagnosis, and delegates execution to the sub-skills' defined workflows.

### 3.2 Required Worker Capabilities

The task that runs this skill should derive/declare:

* `git`
* `gh`

This matches the Task UI capability derivation pattern.

---

## 4. Skill Interface

### 4.1 Inputs (Skill Args)

| Arg                   | Type        |  Default | Meaning                                                                                                              |
| --------------------- | ----------- | -------: | -------------------------------------------------------------------------------------------------------------------- |
| `repo`                | string|null |     null | `owner/repo`. If null, infer from git remote.                                                                        |
| `pr`                  | string|null |     null | PR selector: number, URL, or branch (passed to `gh pr view`).                                                        |
| `branch`              | string|null |     null | Explicit head branch to resolve; if set and `pr` unset, resolve associated PR.                                       |
| `mergeMethod`         | enum        | `squash` | `merge`|`squash`|`rebase`                                                                                            |
| `maxIterations`       | int         |        3 | Guardrail to avoid loops (re-evaluate after each fix).                                                               |
| `failFastIfCiRunning` | bool        |     true | If any CI is in-progress/pending, do not merge and do not attempt CI fixes unless CI is failing (see decision logic) |

### 4.2 Outputs

Write a machine-readable result to artifacts:

* `artifacts/pr_resolver_snapshot.json`
* `artifacts/pr_resolver_result.json`

Result should include:
* Resolved PR identity
* Decision summary (actions taken)
* Merge outcome (merged / skipped / blocked + reason)

---

## 5. Data Collection (Snapshot)

### 5.1 Snapshot Sources

**A. PR metadata**
Use `gh pr view --json` (fields: `number,title,url,isDraft,state,headRefName,baseRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`).

**B. Comments**
Reuse existing scripts (`tools/get_branch_pr_comments.py` or `tools/get_pr_comments.py`) to yield a normalized list of comments.

**C. CI / Checks / Running state**
Implement `bin/pr_resolve_snapshot.py` to emit a unified snapshot, computing:
* `ci.isRunning`
* `ci.hasFailures`
* `ci.failedChecks[]`

---

## 6. Decision Engine

The resolver always re-evaluates from the top after each applied fix (bounded by `maxIterations`).

1. **Preflight stop conditions:** PR not found, PR is draft, or PR already merged/closed.
2. **Merge conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) **or** `mergeStateStatus` is not `CLEAN` → Delegate to `fix-merge-conflicts` skill.
3. **CI failures:** If `ci.hasFailures == true` → Delegate to `fix-ci` skill (or fallback to manual diagnosis if skill missing).
4. **Review comments:** If `reviewDecision` requests changes or comments are actionable → Delegate to `fix-comments` skill.
5. **Merge:** If `mergeable` is clean, `mergeStateStatus` is `CLEAN`, no CI running, and reviews satisfied → Execute `gh pr merge`.
6. **Blocked:** If CI is running without failures and merge state is clean (`mergeable` clean and `mergeStateStatus` is `CLEAN`) → Exit with `blocked_by_ci_running`.

---

## 7. Fix Execution Strategies (Instruction Composition)

Instead of hardcoding git commands or CI logic, the `pr-resolver` acts as an Umbrella Agent. It dynamically reads the instructions of specialized skills and executes them in the current session.

### 7.1 Fix Merge Conflicts
**Action:** The agent reads `.agents/skills/fix-merge-conflicts/SKILL.md` (or equivalent location) into its context.
**Execution:** It follows the step-by-step procedure defined in that file to resolve the git conflicts and push the resolution.

### 7.2 Fix Build Errors / Fix Tests
**Action:** The agent reads `.agents/skills/fix-ci/SKILL.md` into its context.
**Execution:** It follows the instructions to map the failing check to a local command, apply code fixes, verify locally, and push the commit.

### 7.3 Fix Comments (Review Feedback)
**Action:** The agent reads `.agents/skills/fix-comments/SKILL.md` into its context.
**Execution:** It follows the instructions to categorize comments, apply code changes, and optionally reply to the PR.

*Note: If a specific sub-skill does not exist yet (e.g., `fix-ci`), the `pr-resolver` can fallback to general best-effort problem solving based on the snapshot data.*

---

## 8. Merge Behavior

Merge only when:
* PR is open and not draft
* `mergeable` is clean
* CI checks are complete and passing
* **No CI currently running**
* Review policy satisfied

Execution: `gh pr merge <pr> --<mergeMethod>`

---

## 9. Tasks Dashboard Integration

### 9.1 Task Template

Create a Task Step Template named `Resolve PR`.
Important: set publish mode to none, because the skill owns git pushes and merging.

```json
{
  "type": "task",
  "priority": 0,
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "requiredCapabilities": ["codex", "git", "gh"],
    "task": {
      "instructions": "Resolve the current branch PR: fix conflicts/CI/comments, then merge if green and idle.",
      "skill": {
        "id": "pr-resolver",
        "args": { "mergeMethod": "squash" }
      },
      "publish": { "mode": "none" }
    }
  }
}
```

---

## 10. Observability and Artifacts

Write structured artifacts under the run:
* `artifacts/pr_resolver_snapshot.json`
* `artifacts/pr_resolver_result.json`

Include in result:
* `decision`: chosen actions
* `merge`: attempted/blocked + reason

---

## 11. Safety Gates

* Working tree must be clean at start.
* Loop guard: `maxIterations` stops repeated "fix → re-evaluate" loops.
* Merge guard: do not merge if any CI is running.

---

## 12. Suggested `SKILL.md` Skeleton (Agent Skill)

This enforces the "Read-and-Execute" pattern for the LLM:

```markdown
---
name: pr-resolver
description: Master orchestrator to resolve a PR by diagnosing state and delegating to specialized skills.
---

# PR Resolver Skill

## Purpose
You are the master orchestrator for finishing Pull Requests. You diagnose the PR state using a snapshot script and resolve issues by reading and executing the instructions of specialized sub-skills (`fix-merge-conflicts`, `fix-ci`, etc.).

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.maxIterations (default 3)
- inputs.failFastIfCiRunning (default true)

## Workflow
1. Run `bin/pr_resolve_snapshot.py` to generate `artifacts/pr_resolver_snapshot.json`.
2. Inspect the snapshot output.
3. Apply fixes in this strict priority order:
   - **Merge Conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) **or** `mergeStateStatus` is not `CLEAN`, you MUST read `.agents/skills/fix-merge-conflicts/SKILL.md`. Follow its procedure exactly to resolve the conflict.
   - **CI Failures:** If `ci.hasFailures` is true, you MUST read `.agents/skills/fix-ci/SKILL.md` (or similar available skill) and follow its procedure to fix the tests/build.
   - **Review Comments:** If `reviewDecision` indicates changes requested, read `.agents/skills/fix-comments/SKILL.md` and follow its procedure.
   - **Merge:** If all green, `mergeable` is clean, `mergeStateStatus` is `CLEAN`, and NO CI is running, execute `gh pr merge --<mergeMethod>`.
   - **Blocked:** If CI is running but no failures and merge state is clean (`mergeable` clean and `mergeStateStatus` is `CLEAN`), exit and state the PR is blocked waiting for CI.
4. After applying ANY fix (conflict, CI, or review), you MUST loop back to Step 1 and re-run the snapshot. Stop after `maxIterations`.
5. Write `artifacts/pr_resolver_result.json` summarizing the actions taken and the final merge outcome.

## Constraints
- Do NOT try to invent your own conflict resolution or CI fixing workflow. Always load and follow the specialized sub-skill instructions.
- This skill is allowed to commit/push and merge (task.publish.mode MUST be none).
```

---

## 13. Implementation Checklist

1. Add `.agents/skills/pr-resolver/` directory with `SKILL.md`.
2. Implement `bin/pr_resolve_snapshot.py`:
   * resolves PR selector defaulting to current branch
   * emits PR metadata + CI rollup + comment summary
3. Add a Task Step Template entry in the dashboard catalog for `Resolve PR`.
4. Unit test the snapshot script JSON schema and key decision gates (mock `gh` output).
