# Managed Agent Skill: Github PR Resolver Technical Design

Status: Active
Owners: MoonMind Engineering
Last Updated: 2026-07-11

## 1. Purpose

The **PR Resolver** skill is invoked from the dashboard (via Temporal `AgentTaskWorkflow`) to:

1. **Resolve target PR** (defaults to the PR associated with the current branch).
2. **Fetch PR metadata + CI status + comments**.
3. **Diagnose and Delegate**: classify the portable snapshot and follow the selected specialized Skill from the same resolved Skill set.
4. **Merge the PR** if everything is already good **and** no CI is currently running.

MoonMind executes `pr-resolver` as an ordinary resolved Skill in
`MoonMind.AgentRun`. The Skill markdown owns the loop and the packaged portable
helpers own snapshot collection and merge gating. MoonMind supplies the managed
runtime substrate and projects the Skill's terminal artifacts; it does not run a
second resolver implementation.

The checked-in skill is also a portable contract. Its provider-neutral models,
normalization, classification, transition, and evidence rules live in
`pr_resolver_core`, while provider data collection and command execution live in
the Skill bundle. The same bundle is used in direct Codex and MoonMind-managed
runs. The core performs no network, filesystem, process, credential, clock, or
Temporal operations.

---

## 2. Assumptions and Constraints

* The `temporal-worker-sandbox` environment already supports run-scoped skills via `.agents/skills` symlinks to a single active set.
* The worker environment has GitHub auth available for private repo operations and includes `gh` usage in existing workflows.
* Specialized sub-skills (like `fix-merge-conflicts`) are available in the `.agents/skills/` directory.
* Resolver workflows use `publish.mode=auto`. Specialized Skills may commit and
  push when their instructions require it; the portable `pr-resolver` finalize
  helper owns merge and terminal-evidence generation.
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

The Python scripts and this markdown form the portable implementation. MoonMind
materializes and executes that same bundle through its ordinary agent runtime;
there is no active native semantic host.

### 3.2 Skill-owned host contract

The portable contract declares `implementation.contract =
pr-resolver-core/v1`, supports the `cli` host, and is not native-host eligible.
All resolved sources—built-in, deployment, repository, or local—execute their
exact immutable Skill content through the ordinary agent runtime path. Built-in
provenance does not authorize MoonMind to replace the bundle with
`MoonMind.PRResolver` or GitHub-adapter readiness logic.

The former native resolver remains registered only for replay of Temporal
histories that already recorded it. A new workflow records the
`run-pr-resolver-skill-owned-execution-v1` cutover marker and cannot select that
child type.

### 3.3 Required Runtime Capabilities

The selected managed runtime receives the resolved Skill set plus governed
`git` and `gh` capabilities. MoonMind may materialize credentials, isolate the
workspace, supervise the process, enforce cancellation and timeout policy, and
persist artifacts. GitHub reads and merge attempts are initiated by the portable
Skill helpers inside that boundary, not by an integrations activity that
reimplements resolver behavior.

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

The Skill enforces these values in its own loop. MoonMind's runtime timeout and
intervention policies remain an outer safety envelope and do not replace the
Skill's retry semantics.

### 4.2 Outputs

Write a machine-readable result to the Workflow artifact directory:

* `artifacts/pr_resolver_snapshot.json`
* `artifacts/pr_resolver_result.json`

`var/pr_resolver/result.json` and `artifacts/publish_result.json` are the Skill's
authoritative terminal evidence. MoonMind validates and projects those artifacts
into workflow state; assistant prose or process exit alone is not completion
evidence.

Result should include:
* Resolved PR identity
* Decision summary (actions taken)
* Merge outcome (merged / skipped / blocked + reason)
* `mergeAutomationDisposition` for merge automation consumers:
  `merged`, `already_merged`, `reenter_gate`, `manual_review`, or `failed`

`reenter_gate` is terminal for the current resolver process but nonterminal for
its enclosing merge automation. The Skill authors a `gated-continuation/v1`
reason and retry deadline; the workflow transports and durably waits on that
request without reimplementing review or CI semantics. Standalone and
parent-mismatched handoffs fail closed, and detached polling after agent exit is
unsupported.

The canonical workflow terminal dispositions are `merged`, `already_merged`,
`manual_review`, and `failed`. Intermediate waits and remediation dispatches stay
inside workflow state and are not terminal agent dispositions.

---

## 5. Data Collection (Snapshot)

### 5.1 Snapshot Sources

**A. PR metadata**
Use `gh pr view --json` (fields: `number,title,url,isDraft,state,headRefName,baseRefName,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`).

**B. Comments**
The Skill resolves `fix-comments/tools/get_branch_pr_comments.py` from the same
immutable active Skill set. That helper retrieves issue comments, review bodies,
inline review comments, and paginated review-thread resolution/outdated state.
MoonMind workflows, Activities, and GitHub adapters do not retrieve or classify
comments for `pr-resolver`.

**C. CI / Checks / Running state**
`bin/pr_resolve_snapshot.py` emits a unified snapshot, computing:
* `ci.isRunning`
* `ci.hasFailures`
* `ci.failedChecks[]`

---

## 6. Decision Engine

The Skill always re-reads authoritative GitHub state after each applied fix.

1. **Preflight stop conditions:** PR not found, PR is draft, or PR already merged/closed.
2. **Merge conflicts:** If `mergeable` indicates conflict (`false`, `CONFLICTING`, or `DIRTY`) or `mergeStateStatus` is exactly `DIRTY` → Delegate to `fix-merge-conflicts` skill before any CI-fix or CI-wait decision.
3. **CI failures:** If `ci.hasFailures == true` → Delegate to `fix-ci` skill (or fallback to manual diagnosis if skill missing).
4. **Review comments:** If `reviewDecision` requests changes or comments are actionable → Delegate to `fix-comments` skill.
5. **Merge:** If gates pass, run the portable finalize helper with the selected merge method and independently verify the remote merge result through that helper.
6. **Transient wait:** apply the Skill's bounded backoff, then read a new portable snapshot.
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

Execution: `bin/pr_resolve_finalize.py` refreshes the portable snapshot, verifies
the exact head and all gates, invokes `gh pr merge` with the selected method, and
writes terminal evidence.

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

## 12. Canonical Skill Instructions

`.agents/skills/pr-resolver/SKILL.md` is the canonical executable instruction
contract. This document intentionally does not duplicate its step-by-step
workflow. Changes to resolver behavior begin in that Skill bundle and are then
reflected here as durable architecture; MoonMind-native code must not become a
second source of behavior.

---

## 13. Verification

- Skill assets live under `.agents/skills/pr-resolver/`; snapshot logic is exercised by `tests/unit/test_pr_resolver_tools.py` (loads `pr_resolve_snapshot.py` from the skill tree).
- Skill resolution tests require `supportedHosts = ["cli"]` and
  `nativeHostEligible = false`.
- New `MoonMind.UserWorkflow` histories route `pr-resolver` through
  `MoonMind.AgentRun`; the dedicated native workflow is replay-only.
- The dashboard submit flows reference `pr-resolver` in the React workflow-start surface and its focused entrypoint tests.
