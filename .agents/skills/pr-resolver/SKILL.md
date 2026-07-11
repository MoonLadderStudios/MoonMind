---
name: pr-resolver
description: Master orchestrator to resolve a PR by diagnosing state and delegating to specialized skills.
metadata:
  sideEffect:
    kind: merge_pull_request
    owner: agent
    outcomeArtifact: var/pr_resolver/result.json
    terminalContractId: pr_resolver_terminal.v1
    terminalSchemaVersion: moonmind.pr-resolver-result.v1
  publish:
    mode: auto
    owner: agent
    requiresEvidence: true
    verifyRemoteHead: exact
  required-skills: "fix-comments fix-ci fix-merge-conflicts"
  required-capabilities:
    - git
    - gh
---

# PR Resolver Skill

## Purpose
You are the master orchestrator for finishing Pull Requests. Use bounded retries to keep finalize resilient, and delegate actual fixes to specialized skills (`fix-merge-conflicts`, `fix-comments`, `fix-ci`) when blockers are actionable.

The task is not complete until the target PR is merged or is proven already merged. A local fix, local commit, passing local test run, unresolved review reply, or unpushed branch is not a successful PR resolution.

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.maxIterations (default 5, full remediation cap per cycle)
- inputs.finalizeMaxRetries (default 60)
- inputs.finalizeBackoffSeconds (default 30)
- inputs.finalizeMaxSleepSeconds (default 120)
- inputs.finalizeMaxElapsedSeconds (default 7200)
- Retryable/no-progress blockers use at least 5 finalize attempts before
  returning `attempts_exhausted`, unless a hard timeout, hard failure, or
  non-retryable blocker is reached first.
- `ci_running` finalize waits use at least 60 seconds before the next check,
  even when `finalizeBackoffSeconds` is lower.
- If the only visible PR comment/review is from `gemini-code-assist`, the
  resolver treats that as a Codex-review grace window and polls for up to 10
  minutes before merging. Any additional visible comment/review ends the grace
  wait immediately.

## Temporal ownership contract

`MoonMind.PRResolver` owns snapshot polling, classification, durable timers,
retry budgets, merge attempts, remote verification, cancellation, and terminal
evidence. A managed agent must never run `pr_resolve_orchestrate.py` or host the
outer retry loop in a shell process.

When Temporal dispatches this skill as a remediation child, perform exactly the
single classified action named in the instruction. Use the active immutable skill
snapshot, commit and push only when the selected specialized skill requires it,
return structured evidence, and stop. Do not wait for CI, retry merge, dispatch a
second skill, or write a terminal resolver result; the parent workflow does those.

## Terminal Success Contract
Allowed successful terminal states:
- `var/pr_resolver/result.json` has `status=merged`, `merge_outcome=merged`, and `mergeAutomationDisposition=merged`.
- The PR is independently confirmed as already merged after a snapshot/finalize race, with `mergeAutomationDisposition=already_merged`.

## Merge Automation Result Contract
Every terminal `var/pr_resolver/result.json` MUST include `mergeAutomationDisposition`:
- `merged`: the resolver merged the PR.
- `already_merged`: the PR was independently confirmed as already merged.
- `reenter_gate`: the resolver pushed changes and merge automation must wait for readiness on the new head.
- `manual_review`: the resolver stopped on a blocker, exhausted attempts, or needs human follow-up.
- `failed`: the resolver hit a hard execution failure.

Everything else is blocked, failed, or still in-progress. In particular, never finish with `task complete` or a success summary when:
- the PR remains open,
- the branch is ahead of origin,
- a push failed,
- GitHub auth is unavailable,
- `gh pr merge` failed,
- review comments remain actionable,
- CI is running, degraded, or failing,
- mergeability is unknown, dirty, or blocked.

After any local commit-producing remediation, verify the exact current `HEAD` is visible on the remote PR branch before continuing. If `git push`, `gh`, or any GitHub connector path cannot publish the commit, stop as blocked with reason `publish_unavailable`; do not proceed to finalize and do not report success.

Never print raw environment variables while diagnosing GitHub auth or publish failures. Use targeted checks such as `test -n "$GITHUB_TOKEN"` or trusted-tool health calls; do not run `printenv`, `env`, `set`, or equivalent commands that can expose secrets.

When a delegated remediation step cannot publish, overwrite `var/pr_resolver/result.json` before stopping so parent workflows do not report stale gate state. Use `status=blocked`, `merge_outcome=blocked`, `mergeAutomationDisposition=manual_review`, `reason=publish_unavailable`, `final_reason=publish_unavailable`, and `next_step=manual_review`.

## Bounded remediation actions

- `merge_conflicts` selects `fix-merge-conflicts` once.
- `ci_failures` selects `fix-ci` once.
- `actionable_comments` selects `fix-comments` once.
- Transient waits and unknown/manual blockers never launch an agent remediation turn.

After the child ends, Temporal independently checks the remote PR head. A child
must not claim outer-loop success based on local commits, process output, or its
own artifact alone.

## Lightweight Commands
- Finalize-only gate checker:

```bash
python3 "$PR_RESOLVER_SKILL_DIR/bin/pr_resolve_finalize.py" --pr <pr_number_or_branch> --merge-method <merge|squash|rebase>
```

- Full gate classifier (no merge, deterministic state classification):

```bash
python3 "$PR_RESOLVER_SKILL_DIR/bin/pr_resolve_full.py" --pr <pr_number_or_branch> --merge-method <merge|squash|rebase> --max-iterations <maxIterations>
```

## Constraints
- Keep `pr_resolve_finalize.py` as a gate checker; do not add remediation mutations there.
- Do NOT invent custom conflict/CI/comment workflows; always execute the specialized skill instructions.
- Respect retry caps; if retries are exhausted, return `attempts_exhausted` and stop.
- This skill owns publishing under `task.publish.mode = "auto"` and may commit, push, or merge only as required to resolve the target PR. Before reporting success, ensure `artifacts/publish_result.json` exists. The orchestration command normally generates it by running:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" from-pr-resolver-result \
    --result var/pr_resolver/result.json
  ```
- A failed push, missing GitHub auth, or missing remote branch update is an unresolved PR blocker, even if all code changes are committed locally.
- Never run the legacy orchestration command during normal workflow execution.
