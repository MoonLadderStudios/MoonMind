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
    githubOperations: [write, branch_write, review_request]
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
inputSchema:
  type: object
  properties:
    pr:
      type: string
      title: Pull request
      description: >-
        PR number or PR URL. MoonMind requires either this value or a head
        branch so the resolver cannot target the wrong PR.
    branch:
      type: string
      title: Head branch
      description: >-
        PR head branch. MoonMind requires either this value or a PR number or
        URL so the resolver cannot target the wrong PR.
    reviewProvider:
      type: string
      title: Automated review provider
      description: >-
        Provider-neutral name of the automated reviewer that must review every
        head SHA before merge (for example "codex"). Empty or "none" disables
        the review loop.
    requireFreshReview:
      type: boolean
      title: Require a fresh review for every head
      description: >-
        When true, the resolver refuses to merge a head SHA that has no fresh
        review from reviewProvider and asks its owning gate to request one.
    finishMode:
      type: string
      title: Finish mode
      enum:
        - merge
        - fix_only
      default: merge
      description: >-
        "merge" finishes by merging the pull request once the merge gate opens.
        "fix_only" keeps remediating comments, CI, and conflicts but stops once
        nothing resolver-owned is left to address and reports review_clean
        instead of merging.
    returnToGate:
      type: boolean
      default: false
      description: >-
        Derived by a validated MergeAutomation owner. Return CI/provider waits
        with gatedContinuation so the parent retains the candidate and waits
        durably while the agent releases its Provider Profile slot. This value
        does not authorize a standalone workflow to create a continuation owner.
  anyOf:
    - required:
        - pr
    - required:
        - branch
uiSchema: {}
defaults: {}
---

# PR Resolver Skill

## Purpose
You are the master orchestrator for finishing Pull Requests. Use bounded retries to keep finalize resilient, and delegate actual fixes to specialized skills (`fix-merge-conflicts`, `fix-comments`, `fix-ci`) when blockers are actionable.

The task is not complete until the target PR is merged or is proven already merged. A local fix, local commit, passing local test run, unresolved review reply, or unpushed branch is not a successful PR resolution.

The finalize helper's `phase: intermediate` receipt requests continuation of
this Skill. It is never a terminal `manual_review` result. Resume the specified
remediation using the same PR, saved snapshot, and cumulative budgets. Every
host consumes this portable decision; native runtimes may validate the receipt
and continue the agent, but must not reclassify CI or choose fixes themselves.

`inputs.finishMode = fix_only` is the one exception, and it narrows only the
final side effect: every remediation, push, verification, and gate check still
applies, but once nothing is left to address the resolver reports `review_clean`
and stops instead of merging. `fix_only` never relaxes a blocker, never merges,
and never reports success while comments, CI, or conflicts remain actionable.

Because `fix_only` never merges, merge *authorization* is not its blocker. A
merge gate held closed only by a missing human approving review
(`reviewDecision=REVIEW_REQUIRED`) does not stand between `fix_only` and
`review_clean`: no automated action can produce an approval, and the run was
never going to use one. That terminal records
`final_reason=finish_mode_fix_only_awaiting_human_approval` so it stays
distinguishable from a genuinely open gate, and it still requires confirmed
mergeability -- an approval requirement never substitutes for the conflict
check. Under `finishMode=merge` the same state is a durable
`merge_gate_requires_human_approval` blocker, never a transient wait to retry.

## Inputs (skill args)
- inputs.repo (optional)
- inputs.pr (optional)
- inputs.branch (optional)
- inputs.mergeMethod (merge|squash|rebase)
- inputs.reviewProvider (default empty; `codex` enables the Codex review loop)
- inputs.requireFreshReview (default false; true requires a fresh review per head)
- inputs.finishMode (`merge` default, or `fix_only` to stop at an open merge
  gate without merging)
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
- Provider-specific review protocols (bot message text, reaction kinds,
  grace windows) are adapter data owned by the portable bundle's provider
  adapter/helper, not model-specific reasoning in this workflow. The resolver
  applies the configured provider's freshness/observation rules exactly as
  the adapter reports them for the current head, without hardcoding any
  provider's text or timing in this file.

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
2. Run the finalize gate checker. Before any remediation or completion it
   collects PR metadata, CI, the complete comment inventory, and automated-review
   evidence for the exact head SHA, then revalidates the target and head.
   The existing branch API supplies the base commit, including on older GitHub
   CLI versions without a `baseRefOid` JSON field. Reuse that metadata for branch
   requirements, and verify the base and effective requirements again after
   collecting the inventory. Requirements participate in the wait fingerprint,
   so protection/rules changes cannot leave a formerly required status waiting.
   An unchanged `ci_running` retry reads only PR/base metadata and exact-head CI
   observations (including Actions workflow/attempt evidence when needed).
   Its explicitly wait-only snapshot cannot authorize remediation, a clean
   receipt, or merge. A changed observation or review policy forces a full
   refresh. Direct snapshot and full-classifier commands always refresh fully.
   Review/reaction retrieval errors
   must fail with their diagnostics; they are never evidence of a pending or
   absent review. Always pass the review-loop inputs
   exactly as supplied; omitting them silently disables the fresh-review
   requirement:

   ```bash
   python3 "$PR_RESOLVER_SKILL_DIR/bin/pr_resolve_finalize.py" \
     --pr <pr_number_or_branch> \
     --merge-method <merge|squash|rebase> \
     --review-provider <inputs.reviewProvider or ""> \
     --require-fresh-review \
     --finish-mode <merge|fix_only> \
     --strict-exit-codes
   ```

   Use `--no-require-fresh-review` when `inputs.requireFreshReview` is false or
   absent. Pass `--finish-mode` exactly as supplied by `inputs.finishMode`;
   omitting it defaults to `merge`, which grants merge authority this run may
   not have. `PR_RESOLVER_REVIEW_PROVIDER`, `PR_RESOLVER_REQUIRE_FRESH_REVIEW`,
   and `PR_RESOLVER_FINISH_MODE` are equivalent environment defaults for
   non-agent automation.

3. Read `var/pr_resolver/result.json` and perform exactly the indicated action:
   - `review_clean`: `finishMode` is `fix_only` and nothing resolver-owned is
     left to address -- no conflicts, no CI failures, no actionable or deferred
     comments, and a fresh automated review for this head. Publish terminal
     evidence for the verified branch head and stop without merging, whether or
     not the merge gate would authorize a merge.
   - `merged` or independently verified `already_merged`: publish terminal
     evidence and stop. A successful `gh pr merge` request is not terminal
     evidence until a fresh `gh pr view` reports `state=MERGED`; merge-queue or
     still-open states remain transient.
   - `merge_conflicts`: the PR either conflicts with or is behind its base
     branch; follow `fix-merge-conflicts` completely with the PR's actual
     base branch (`inputs.base`), then push the synchronized branch. Never
     substitute `origin/main` for a PR targeting another base.
   - `ci_failures`: follow `fix-ci` completely.
   - `ci_infra_transient`: every failed check on this head is a GitHub Actions
     platform failure (artifact storage quota, lost runner, Actions service
     error) that no PR change can fix. Finalize already reran the failed jobs
     on the same head when they were due, and its `gatedContinuation` carries
     the wait. Do not start `fix-ci`, edit code, or rerun CI yourself; wait the
     reported `retryAfterSeconds` (or return `reenter_gate` to an owning gate)
     and return to step 2.
   - `ci_infra_rerun_exhausted`: the same platform failure persisted through
     the bounded reruns. Publish the finalize result unchanged; its `decision`
     names the outage and the attempts.
   - `ci_infra_rerun_failed`: GitHub refused the rerun itself (for example the
     token lacks Actions write access). Publish the finalize result unchanged;
     its `decision` carries GitHub's error.
   - `ci_workflow_terminal`: queued or running check jobs belong to a workflow
     GitHub already reports as completed. Preserve the workflow/check evidence
     and report the external CI blocker; do not wait forever for stranded jobs
     or edit the PR to manufacture a passing check. Reconcile CI with its owner
     before a new full gate check.
   - `actionable_comments`: follow `fix-comments` completely, including fresh
     comment retrieval, its disposition ledger, push verification, and resolving
     handled current review threads on GitHub. Every current finding needs an
     applicability decision, including P2 and lower priorities. Severity alone
     never dismisses a finding; use the existing addressed/not-applicable
     dispositions and verified resolved/outdated thread evidence.
   - `fresh_review_required_after_remediation`: the current head SHA has no
     fresh review from the configured provider and none has been requested for
     it. Publish `mergeAutomationDisposition=request_review` with the typed
     `gatedContinuation` and stop. Never post the review request yourself: the
     owning gate owns that side effect and the durable wait for its result.
   - `automated_review_wait`: a request for this head already exists and its
     result has not arrived. Return control to the owning gate with
     `reenter_gate`. This wait takes precedence over every remediation,
     including CI failures, merge conflicts, and older actionable comments.
     Terminal evidence and deferred-comment blockers still take precedence.
     Do not edit, commit, push, or start a fix Skill while it is pending.
   - `automated_review_request_failed`: the provider's latest answer to the
     request for this head refused it (for example a usage limit), and no
     submitted review or clean reaction completed it. Waiting cannot produce
     the review. Publish `manual_review` and stop without starting a fix Skill;
     a newer request made once the provider accepts work supersedes the refusal.
   - `deferred_comments`: the comment ledger deferred or could not fix at least
     one comment that is still present. Publish `manual_review` and stop; a
     repeated remediation pass cannot clear a deferred disposition.
   - `ci_running`, provider-grace waits reported by the portable adapter (for
      example the snapshot's review-grace classification), or another
      documented transient: when the admitted owner supplied `returnToGate`,
      pass `--return-to-gate` to `pr_resolve_orchestrate.py`, retain its
      `gatedContinuation`, and stop this agent turn with `reenter_gate`. The
      existing parent owns the durable wait and subsequent resolver attempt.
      A standalone invocation retains the bounded foreground backoff, then
      returns to step 2. Tactics CI stays on its self-hosted runners; runner
      queueing is an external wait, never a reason to change runner selection.
   - any unavailable, ambiguous, permission-sensitive, or non-retryable state:
     publish `manual_review` or `failed` evidence and stop without merging.
4. After every remediation, verify the exact local `HEAD` is visible on the PR
   branch, then return to step 2. Never reuse a pre-remediation snapshot.
   Completion accepts only the configured provider adapter's qualified
   signals for the current head: a submitted provider review, the adapter's
   qualified request-bound clean-review signal, or its qualified automatic
   completion summary with an associated no-findings reaction, as defined by
   the portable bundle's provider adapter/helper (which
   owns message-text and reaction interpretation). Read all pages and refresh
   the comment inventory after observing completion and revalidate the remote
   head before accepting the snapshot. Merge with the verified head as an
   expected-head guard. A clean review signal satisfies review freshness; it does
   not erase independent actionable findings, CI failures, or conflicts. When
   those gates are clear, finish on the same head without another request.
   Codex's automatic PR-opened summary is completion only when its trusted bot
   identity, exact repository/PR URL, completed Code Review table, and chronology
   are qualified. Its commit must be the current head; an abbreviation must
   uniquely match the complete PR commit inventory and resolve to that exact
   SHA through GitHub. For more than 250 commits, use the paginated comparison
   of the exact PR base/head SHAs rather than the capped PR commits endpoint;
   incomplete counts remain blocked. Automatic chronology uses GitHub's
   comment/completion/reaction times and exact-SHA binding, never author-controlled
   Git commit dates. Undated or malformed explicit requests make automatic
   chronology unavailable, and a later dated request still owns the review wait.
   The bot's PR thumbs-up must postdate completion and the
   summary update. If a reaction labels its actor as User, accept it only when
   GitHub's account lookup confirms the exact login, numeric ID, node ID and
   Bot type; failed lookups stay observable. Summary tables, old thumbs-up,
   eyes/start events, human
   copies, ambiguous commits, and newer unclassified provider feedback cannot
   open the gate. A newer explicit request still owns its unchanged head, and a
   later provider refusal stays failed until qualified newer completion exists.
   The snapshot records the summary, resolved commit and confirming reaction
   identities in automaticSummary; it refreshes the full inventory and checks
   the remote head again before accepting that evidence.
   The comment collector reads submitted reviews and issue replies before the
   inline/thread inventory, and the snapshot reuses those raw reviews. A clean
   issue reply or reaction observed afterward still requires another inventory
   refresh, including review bodies. CI supersession
   requires the same head, workflow, event, app and check name, with matching
   observed PR/base context and nonempty matching head branches across runs;
   a later unrelated or unproven check never erases an older failure.
   Same-run reruns require verified job
   attempt evidence. Missing checks, unknown states and unresolved identities
   stay blocked, and security checks remain gating.
   Forward `finishMode`, review provider/policy, and freshness settings
   through every resolver invocation, delegated step, retry, and documented
   command: a requested review result must match the current head, and
   `fix_only` must never gain merge authority from an omitted flag or
   default.
5. Enforce `maxIterations`, `finalizeMaxRetries`, and
   `finalizeMaxElapsedSeconds`. Retryable/no-progress states receive at least five
   finalize checks unless the elapsed-time limit or a hard failure is reached.
6. Before reporting success, generate `artifacts/publish_result.json` from the
   final resolver result with the shared publish-evidence helper.

## Terminal Success Contract
Allowed successful terminal states:
- `var/pr_resolver/result.json` has `status=merged`, `merge_outcome=merged`, and `mergeAutomationDisposition=merged`.
- The PR is independently confirmed as already merged after a snapshot/finalize race, with `mergeAutomationDisposition=already_merged`.
- `finishMode=fix_only` cleared every resolver-owned blocker:
  `status=review_clean`, `merge_outcome=skipped`, and
  `mergeAutomationDisposition=review_clean`. This state requires no conflicts, no
  CI failures, no actionable or deferred comments, and a fresh automated review
  for the verified head; it is never a way to report an unresolved blocker as
  success. A merge gate awaiting a human approving review is not a resolver-owned
  blocker, because `fix_only` never merges.

## Merge Automation Result Contract
Every terminal `var/pr_resolver/result.json` MUST include `mergeAutomationDisposition`:
- `merged`: the resolver merged the PR.
- `already_merged`: the PR was independently confirmed as already merged.
- `review_clean`: `finishMode` is `fix_only` and no actionable comments, CI
  failures, or conflicts are left for the freshly reviewed head. The resolver did
  not merge and must not claim a merge.
- `request_review`: the current head SHA needs one fresh automated review from
  the configured provider before merge is allowed. Include a
  `gated-continuation/v2` `gatedContinuation` naming only the provider, the
  exact head SHA, and the execution reference. The Skill never supplies request
  text and never posts the request; the validated
  `MoonMind.MergeAutomation` parent translates the provider into its configured
  exact command, performs the request idempotently, and owns the wait.
  Standalone resolvers cannot use this disposition successfully.
- `reenter_gate`: the current resolver child completed a typed handoff to its
  validated `MoonMind.MergeAutomation` parent. Include `gatedContinuation` with
  the reason and an absolute UTC `notBefore` deadline when the next cycle must
  wait. For provider review-grace waits, the direct finalizer copies the
  snapshot's original provider-grace `expiresAt`; it never restarts that deadline.
  Standalone resolvers cannot use this disposition successfully. Provider,
  authentication, rate-limit, infrastructure, timeout, cancellation, stale
  evidence, and malformed evidence failures are never cleared by continuation
  metadata; only the synthetic `PR_RESOLVER_REENTER_GATE` classification may be
  cleared by an authorized handoff.
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

Long waits have exactly two supported ownership patterns: keep the resolver
command in the foreground with periodic progress, or write the authoritative
typed continuation and return control to a validated merge-automation parent.
Never start detached polling, emit the final agent response, and claim the poll
will continue after the managed CLI exits. Background task identifiers in output
have no lifecycle authority.

After any local commit-producing remediation, verify the exact current `HEAD` is visible on the remote PR branch before continuing. If `git push`, `gh`, or any GitHub connector path cannot publish the commit, stop as blocked with reason `publish_unavailable`; do not proceed to finalize and do not report success. Before repeating a push or merge whose outcome is uncertain, reconcile first: re-read the remote branch head and PR merge state and continue from the reconciled state instead of repeating the write blindly. An accepted merge request, queued check, local commit, missing auth, or stale snapshot is not success: only a fresh `gh pr view` reporting `state=MERGED` for the verified head is terminal merge evidence. Auxiliary output failures never erase a verified primary effect and never cause another push/merge on their own.

Resolve the selected PR's exact repository, head, base, and allowed finish mode from authoritative inputs before any mutation. Preserve source authority for fork or otherwise unsupported combinations and stop before unsafe mutation. Revalidate relevant identity changes before writes, using only existing authorized identity configuration.

Never print raw environment variables while diagnosing GitHub auth or publish failures. Use targeted checks such as `test -n "$GITHUB_TOKEN"` or trusted-tool health calls; do not run `printenv`, `env`, `set`, or equivalent commands that can expose secrets.

When a delegated remediation step cannot publish, overwrite `var/pr_resolver/result.json` before stopping so parent workflows do not report stale gate state. Use `status=blocked`, `merge_outcome=blocked`, `mergeAutomationDisposition=manual_review`, `reason=publish_unavailable`, `final_reason=publish_unavailable`, and `next_step=manual_review`.

## Bounded remediation actions

- `merge_conflicts`, including a conflict-free PR reported as `BEHIND`, selects
  `fix-merge-conflicts` once.
- `ci_failures` selects `fix-ci` once. Infrastructure-only CI failures are
  `ci_infra_transient`, never `ci_failures`, and never launch `fix-ci`.
- `actionable_comments` selects `fix-comments` once.
- `fresh_review_required_after_remediation` never launches a remediation turn;
  it is a typed request to the owning gate.
- Transient waits and unknown/manual blockers never launch an agent remediation turn.

After a specialized Skill finishes, `pr-resolver` independently re-runs its
portable gate against the remote PR head. A remediation Skill must not claim
outer-loop success based on local commits, process output, or its own artifact
alone.

## Lightweight Commands
- Finalize-only gate checker (pass finish/review options exactly as supplied;
  omitting `--finish-mode` defaults to `merge` and omitting review flags
  disables the fresh-review requirement):

```bash
python3 "$PR_RESOLVER_SKILL_DIR/bin/pr_resolve_finalize.py" --pr <pr_number_or_branch> --merge-method <merge|squash|rebase> --review-provider <provider or ""> --require-fresh-review --finish-mode <merge|fix_only>
```

Use `--no-require-fresh-review` when fresh reviews are not required.

- Full gate classifier (no merge, deterministic state classification; pass
  the review inputs exactly as supplied so classification matches the merge
  gate):

```bash
python3 "$PR_RESOLVER_SKILL_DIR/bin/pr_resolve_full.py" --pr <pr_number_or_branch> --merge-method <merge|squash|rebase> --max-iterations <maxIterations> --review-provider <provider or ""> --require-fresh-review
```

## Constraints
- Keep `pr_resolve_finalize.py` as a gate checker; do not add remediation mutations there. Its only CI write is rerunning the failed jobs of an infrastructure-only failure on the unchanged head (at most three attempts per run, quota failures after a 30-minute backoff); that maintains the gate and never changes the PR.
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
