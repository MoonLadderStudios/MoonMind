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
  implementation:
    contract: pr-resolver-core/v1
    supportedHosts:
      - cli
    nativeHostEligible: false
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

## Skill authority and host boundary

This resolved Skill bundle is the sole semantic implementation of
`pr-resolver`, in MoonMind and outside it. The instructions in this file and the
portable helpers beside it own GitHub snapshot collection, comment retrieval,
comment actionability, CI interpretation, blocker priority, retry decisions,
specialized-skill selection, merge gating, and terminal-result construction.

MoonMind must execute this Skill through the ordinary agent Skill path. Native
integration may resolve and materialize the immutable Skill bundle, provide the
workspace and credentials, launch and supervise the runtime, enforce timeout or
cancellation policy, capture logs, and persist or validate terminal artifacts.
It must not replace any behavior listed above with MoonMind-only workflow,
activity, adapter, or GitHub-client logic. In particular, MoonMind must not
collect or classify PR comments on behalf of this Skill.

If a host cannot execute the resolved Skill bundle and its required Skills, it
must fail before mutation. It must never substitute a built-in implementation
based on the `pr-resolver` name, source, publish mode, or an implementation
metadata flag.

## Workflow

1. Resolve the active `pr-resolver` directory and use only the helpers from that
   immutable active Skill set. Resolve `fix-comments`, `fix-ci`, and
   `fix-merge-conflicts` from the same active set; do not use repo-global or
   host-native substitutes. MoonMind exports the active root as
   `MOONMIND_ACTIVE_SKILLS_DIR`. Outside MoonMind, set `PR_RESOLVER_SKILL_DIR` to
   the directory containing this `SKILL.md`. Establish the portable paths before
   running a helper:

   ```bash
   PR_RESOLVER_SKILL_DIR="${PR_RESOLVER_SKILL_DIR:-${MOONMIND_ACTIVE_SKILLS_DIR:+$MOONMIND_ACTIVE_SKILLS_DIR/pr-resolver}}"
   ACTIVE_SKILLS_DIR="${MOONMIND_ACTIVE_SKILLS_DIR:-$(dirname "$PR_RESOLVER_SKILL_DIR")}"
   test -n "$PR_RESOLVER_SKILL_DIR" && test -f "$PR_RESOLVER_SKILL_DIR/SKILL.md"
   ```
2. Run the finalize gate checker. It refreshes PR metadata, CI, and the complete
   comment inventory before deciding whether merge is allowed:

   ```bash
   python3 "$PR_RESOLVER_SKILL_DIR/bin/pr_resolve_finalize.py" \
     --pr <pr_number_or_branch> \
     --merge-method <merge|squash|rebase> \
     --strict-exit-codes
   ```

3. Read `var/pr_resolver/result.json` and perform exactly the indicated action:
   - `merged` or independently verified `already_merged`: publish terminal
     evidence and stop. A successful `gh pr merge` request is not terminal
     evidence until a fresh `gh pr view` reports `state=MERGED`; merge-queue or
     still-open states remain transient.
   - `merge_conflicts`: follow `fix-merge-conflicts` completely.
   - `ci_failures`: follow `fix-ci` completely.
   - `actionable_comments`: follow `fix-comments` completely, including fresh
     comment retrieval, its disposition ledger, push verification, and resolving
     handled current review threads on GitHub.
   - `ci_running`, `codex_review_grace_wait`, or another documented transient:
     wait with the bounded backoff configured by the Skill inputs, then return to
     step 2.
   - any unavailable, ambiguous, permission-sensitive, or non-retryable state:
     publish `manual_review` or `failed` evidence and stop without merging.
4. After every remediation, verify the exact local `HEAD` is visible on the PR
   branch, then return to step 2. Never reuse a pre-remediation snapshot.
5. Enforce `maxIterations`, `finalizeMaxRetries`, and
   `finalizeMaxElapsedSeconds`. Retryable/no-progress states receive at least five
   finalize checks unless the elapsed-time limit or a hard failure is reached.
6. Before reporting success, generate `artifacts/publish_result.json` from the
   final resolver result with the shared publish-evidence helper.

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

After a specialized Skill finishes, `pr-resolver` independently re-runs its
portable gate against the remote PR head. A remediation Skill must not claim
outer-loop success based on local commits, process output, or its own artifact
alone.

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
- Do not ask MoonMind or another host to collect, summarize, or classify GitHub comments for this Skill.
- Respect retry caps; if retries are exhausted, return `attempts_exhausted` and stop.
- This skill owns publishing under `task.publish.mode = "auto"` and may commit, push, or merge only as required to resolve the target PR. Before reporting success, ensure `artifacts/publish_result.json` exists. The orchestration command normally generates it by running:
  ```bash
  python3 "$ACTIVE_SKILLS_DIR/_shared/publish_evidence.py" from-pr-resolver-result \
    --result var/pr_resolver/result.json
  ```
- A failed push, missing GitHub auth, or missing remote branch update is an unresolved PR blocker, even if all code changes are committed locally.
- `pr_resolve_orchestrate.py` is a portable utility for non-agent automation and
  tests. An agent executing this markdown owns specialized-skill dispatch and
  must follow the workflow above rather than assuming that utility can perform
  agent remediation.
