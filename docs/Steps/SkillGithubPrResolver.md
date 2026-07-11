# Managed Agent Skill: Github PR Resolver Technical Design

Status: Active
Owners: MoonMind Engineering
Last Updated: 2026-07-11

## 1. Purpose

The **PR Resolver** skill is invoked from the dashboard (via Temporal `AgentTaskWorkflow`) to:

1. **Resolve target PR** (defaults to the PR associated with the current branch).
2. **Fetch PR metadata + CI status + comments**.
3. **Diagnose and Delegate**: let `MoonMind.PRResolver` classify durable gate state and launch one bounded `MoonMind.AgentRun` for the selected specialized skill.
4. **Merge the PR** if everything is already good **and** no CI is currently running.

The durable umbrella is a Temporal workflow, not a managed agent shell. Specialized agents do one remediation action; Temporal owns polling, timers, retry budgets, merge attempts, remote verification, cancellation, and terminal evidence.

The checked-in skill is also a portable contract. Its provider-neutral models,
normalization, classification, transition, and evidence rules live in
`pr_resolver_core`; both the local scripts and the Temporal host consume that
package. The core performs no network, filesystem, process, credential, clock,
or Temporal operations. Host adapters gather state and perform actions.

---

## 2. Assumptions and Constraints

* The `temporal-worker-sandbox` environment already supports run-scoped skills via `.agents/skills` symlinks to a single active set.
* The worker environment has GitHub auth available for private repo operations and includes `gh` usage in existing workflows.
* Specialized sub-skills (like `fix-merge-conflicts`) are available in the `.agents/skills/` directory.
* Resolver workflows use `publish.mode=auto`. Remediation children may commit and push when their selected skill requires it; trusted resolver activities own merge and terminal publication.
* Publish authority and implementation hosting are independent decisions.
  `publish.mode=auto` does not authorize native Temporal execution.

---

## 3. Skill packaging

### 3.1 Layout

Shared repo skill mirror:

```
.agents/skills/pr-resolver/
  SKILL.md
  bin/
    pr_resolve_snapshot.py
    pr_resolve_contract.py
    pr_resolve_finalize.py
    pr_resolve_full.py
    pr_resolve_orchestrate.py
  schemas/
    pr_resolver_snapshot.schema.json
    pr_resolver_result.schema.json
```

The Python scripts remain a supported portable host. MoonMind routes the trusted
built-in snapshot to `MoonMind.PRResolver`; standalone environments and
non-native-compatible snapshots use the portable host.

### 3.2 Trusted native binding

The portable contract declares `implementation.contract =
pr-resolver-core/v1`, supported hosts, and native-host policy separately from
publish metadata. Native routing requires the immutable resolved-skill entry to
prove all of the following: canonical name, compatible contract, Temporal host
support, trusted policy, built-in provenance, and non-empty content ref and
digest. A repository or local override named `pr-resolver` does not inherit the
native workflow or trusted privileges. It runs through the explicit CLI fallback
with an observable reason code, or is rejected before launch when policy forbids
that host.

Temporal workers load `pr_resolver_core` from their immutable application
artifact. They never import repository skill code during workflow replay.

### 3.3 Required Worker Capabilities

The workflow fleet is orchestration-only and has no `git`, `gh`, repository
write, sandbox, or agent-runtime privilege. GitHub reads and merge attempts run
on the integrations activity fleet; remediation runs in bounded
`MoonMind.AgentRun` children.

---

## 4. Skill Interface

### 4.1 Inputs (Skill Args)

| Arg                   | Type        |  Default | Meaning                                                                                                              |
| --------------------- | ----------- | -------: | -------------------------------------------------------------------------------------------------------------------- |
| `repo`                | string|null |     null | `owner/repo`. If null, infer from git remote.                                                                        |
| `pr`                  | string|null |     null | PR selector: number, URL, or branch (passed to `gh pr view`).                                                        |
| `branch`              | string|null |     null | Explicit head branch to resolve; if set and `pr` unset, resolve associated PR.                                       |
| `mergeMethod`         | enum        | `squash` | `merge`|`squash`|`rebase`                                                                                            |
| `maxIterations`             | int         |        5 | Guardrail to avoid loops (re-evaluate after each fix).                                                                            |
| `finalizeMaxRetries`        | int         |       60 | Total retries allowed for the orchestration process, including both finalize-only waits and full remediation cycles.              |
| `finalizeBackoffSeconds`    | int         |       30 | Base sleep for finalize-only retries. The orchestrator uses exponential backoff and caps each sleep at `finalizeMaxSleepSeconds`. |
| `finalizeMaxSleepSeconds`   | int         |      120 | Max sleep between finalize-only retries.                                                                                            |
| `finalizeMaxElapsedSeconds` | int         |     7200 | Hard wall-clock cap for one orchestration run.                                                                                      |

These values compile into `PRResolverPolicyModel`. Temporal persists counters and
uses durable timers; no managed agent process sleeps between polls.

### 4.2 Outputs

Write a machine-readable result to the Workflow artifact directory:

* `artifacts/pr_resolver_snapshot.json`
* `artifacts/pr_resolver_result.json`

Temporal state and artifact refs are authoritative. The terminal publication
activity continues to name compatibility artifacts `var/pr_resolver/result.json`
and `artifacts/publish_result.json`; those names do not make a workspace shell
process authoritative.

Result should include:
* Resolved PR identity
* Decision summary (actions taken)
* Merge outcome (merged / skipped / blocked + reason)
* `mergeAutomationDisposition` for merge automation consumers:
  `merged`, `already_merged`, `reenter_gate`, `manual_review`, or `failed`

The canonical workflow terminal dispositions are `merged`, `already_merged`,
`manual_review`, and `failed`. Intermediate waits and remediation dispatches stay
inside workflow state and are not terminal agent dispositions.

---

## 5. Data Collection (Snapshot)

### 5.1 Snapshot Sources

**A. PR metadata**
Use `gh pr view --json` (fields: `number,title,url,isDraft,state,headRefName,baseRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`).

**B. Comments**
Reuse existing scripts (`tools/get_branch_pr_comments.py` or `tools/get_pr_comments.py`) to yield a normalized list of comments.

**C. CI / Checks / Running state**
`bin/pr_resolve_snapshot.py` emits a unified snapshot, computing:
* `ci.isRunning`
* `ci.hasFailures`
* `ci.failedChecks[]`

---

## 6. Decision Engine

The workflow always re-reads authoritative GitHub state after each applied fix.

1. **Preflight stop conditions:** PR not found, PR is draft, or PR already merged/closed.
2. **Merge conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) or `mergeStateStatus` is exactly `DIRTY` → Delegate to `fix-merge-conflicts` skill before any CI-fix or CI-wait decision.
3. **CI failures:** If `ci.hasFailures == true` → Delegate to `fix-ci` skill (or fallback to manual diagnosis if skill missing).
4. **Review comments:** If `reviewDecision` requests changes or comments are actionable → Delegate to `fix-comments` skill.
5. **Merge:** If gates pass, execute one idempotent `pr_resolver.finalize_merge` activity and independently run `pr_resolver.verify_merged`.
6. **Transient wait:** schedule a durable Temporal timer, then read a new snapshot.
7. **Blocked:** stop on budget exhaustion, non-retryable input, or an identical actionable blocker that repeats without a remote head change.

---

## 7. Fix Execution Strategies (Instruction Composition)

The resolver starts one bounded `MoonMind.AgentRun` with the immutable resolved
skill-set ref and exact blocker/head identity. It never embeds specialized repair
logic in workflow code.

### 7.1 Fix Merge Conflicts
**Action:** The agent reads `.agents/skills/fix-merge-conflicts/SKILL.md` (or equivalent location) into its context.
**Execution:** It follows the step-by-step procedure defined in that file to resolve the git conflicts and push the resolution.

### 7.2 Fix Build Errors / Fix Tests
**Action:** The agent reads `.agents/skills/fix-ci/SKILL.md` into its context.
**Execution:** It follows the instructions to map the failing check to a local command, apply code fixes, verify locally, and push the commit.

### 7.3 Fix Comments (Review Feedback)
**Action:** The agent reads `.agents/skills/fix-comments/SKILL.md` into its context.
**Execution:** It follows the instructions to categorize comments, apply code changes, and optionally reply to the PR.

If the required specialized skill is unavailable, resolution fails closed instead
of substituting general best-effort behavior.

---

## 8. Merge Behavior

Merge only when:
* PR is open and not draft
* `mergeable` is clean
* CI checks are complete and passing
* **No CI currently running**
* Review policy satisfied

Execution: the bounded `pr_resolver.finalize_merge` activity calls the trusted
GitHub adapter with an exact expected head and stable idempotency key.

---

## 9. Dashboard integration

### 9.1 Example `AgentTaskWorkflow` payload

Use an `AgentTaskWorkflow` with `publish.mode` `auto`, because the skill owns git pushes and merging inside the agent loop and must produce auto publish evidence.

```json
{
  "repository": "MoonLadderStudios/MoonMind",
  "requiredCapabilities": ["git", "gh"],
  "task": {
    "instructions": "Resolve the current branch PR: fix conflicts/CI/comments, then merge if green and idle.",
    "skill": {
      "id": "pr-resolver",
      "args": { "mergeMethod": "squash" }
    },
    "publish": { "mode": "auto" }
  }
}
```

---

## 10. Observability and Artifacts

Write structured artifacts under the Temporal Artifact directory:
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
* Transient finalize blockers such as `ci_running` use bounded exponential backoff rather than failing immediately.

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
You are the Master orchestrator for finishing Pull Requests. You diagnose the PR state using a snapshot script and resolve issues by reading and executing the instructions of specialized sub-skills (`fix-merge-conflicts`, `fix-ci`, etc.).

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.maxIterations (default 5)
- inputs.finalizeMaxRetries (default 60)
- inputs.finalizeBackoffSeconds (default 30)
- inputs.finalizeMaxSleepSeconds (default 120)
- inputs.finalizeMaxElapsedSeconds (default 7200)

## Workflow
1. Run `bin/pr_resolve_snapshot.py` to generate `artifacts/pr_resolver_snapshot.json`.
2. Inspect the snapshot output.
3. Apply fixes in this strict priority order:
   - **Merge Conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) or `mergeStateStatus` is exactly `DIRTY`, you MUST read `.agents/skills/fix-merge-conflicts/SKILL.md`. Follow its procedure exactly to resolve the conflict before attempting CI fixes or waiting for CI.
   - **CI Failures:** If `ci.hasFailures` is true, you MUST read `.agents/skills/fix-ci/SKILL.md` (or similar available skill) and follow its procedure to fix the tests/build.
   - **Review Comments:** If `reviewDecision` indicates changes requested, read `.agents/skills/fix-comments/SKILL.md` and follow its procedure.
   - **Merge:** If all green, `mergeable` is clean, `mergeStateStatus` is `CLEAN`, and NO CI is running, execute `gh pr merge --<mergeMethod>`.
   - **Finalize-only retry:** If CI is running but no failures while `mergeable` is clean and `mergeStateStatus` is exactly `CLEAN`, retry finalize after bounded exponential backoff until the transient retry budget is exhausted.
4. After applying ANY fix (conflict, CI, or review), you MUST loop back to Step 1 and re-run the snapshot. Stop after `maxIterations`, but do not report `attempts_exhausted` for retryable/no-progress blockers before five finalize attempts unless a hard timeout, hard failure, or non-retryable blocker is reached first.
5. Write `artifacts/pr_resolver_result.json` summarizing the actions taken and the final merge outcome.

## Constraints
- Do NOT try to invent your own conflict resolution or CI fixing workflow. Always load and follow the specialized sub-skill instructions.
- This skill is allowed to commit/push and merge only under `task.publish.mode = "auto"` and must write `artifacts/publish_result.json` evidence before reporting success.
```

---

## 13. Verification

- Skill assets live under `.agents/skills/pr-resolver/`; snapshot logic is exercised by `tests/unit/test_pr_resolver_tools.py` (loads `pr_resolve_snapshot.py` from the skill tree).
- The dashboard submit flows reference `pr-resolver` in the React workflow-start surface and its focused entrypoint tests.
