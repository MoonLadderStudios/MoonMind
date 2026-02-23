---
name: fix-comments
description: Resolve GitHub PR feedback end-to-end for the branch you are on. Use when you need to fetch all comments on the branch PR, evaluate whether each comment still applies, decide whether it should be addressed, implement fixes, run compile/tests with retry-on-failure, then commit and push the result.
---

# Fix Comments

Run this as a full remediation workflow for the active branch PR. Do not stop after analysis.

## Inputs

- Optional: explicit scope constraints from the user (for example, "only fix blocking items").
- Optional: preferred commit message.

If no constraints are provided, default to addressing all applicable feedback.

## Workflow

1. Resolve PR and collect all comments.
- Run `python3 tools/get_branch_pr_comments.py --output Saved/PRComments/current-branch-comments.json`.
- If PR resolution fails, stop and ask the user for a PR number/URL.
- Load `Saved/PRComments/current-branch-comments.json` and treat every entry in `comments` as input feedback.

2. Build a feedback ledger before editing code.
- Create a working checklist with one row per comment:
  - `id`, `type`, `author`, `url`, `path:line` (if present), `summary`, `still_applies`, `should_address`, `action_plan`.
- Keep replies (`in_reply_to_id`) tied to their parent comment to avoid duplicate work.
- Never silently drop a comment.

3. Decide if each comment still applies.
- For code-line comments:
  - Inspect current file state and surrounding logic, not just the old line number.
  - Mark `still_applies=false` only when the concern is already fixed or made irrelevant by later changes.
- For broad review/issue comments:
  - Compare against current behavior, tests, and architecture constraints.
- Record a one-sentence rationale for each `still_applies=false` decision.

4. Decide whether each applicable comment should be addressed now.
- Default to `should_address=true` for correctness, crashes, determinism, networking, security, data loss, CI stability, and test gaps.
- `should_address=false` is allowed only when:
  - It conflicts with explicit user direction, or
  - It requires product/design decisions outside current scope.
- Record rationale for each skipped item and keep the skipped list in final output.

5. Implement fixes with the smallest safe change.
- Process one actionable item at a time.
- Prefer root-cause fixes over cosmetic edits.
- Add or update tests whenever behavior changes.
- Re-check adjacent comments after each fix to collapse duplicates.

6. Run compile/tests and retry until green.
- Follow repo AGENTS guidance for local Unreal validation:
  - Compile only if platform build entrypoint and toolchain exist.
  - Run targeted automation tests when `UnrealEditor-Cmd` is available.
- If compilation/tests fail:
  - Read logs,
  - Fix the failures,
  - Re-run compile/tests.
- Repeat until the touched scope is passing locally, or until blocked by missing environment prerequisites. If blocked, report exact blocker.

7. Finalize and push.
- Ensure every comment is classified in the ledger (`addressed`, `not-applicable`, or `deferred-with-reason`).
- Run a final `git status` review.
- Commit with a clear message (default: `Address PR feedback for #<number>`).
- Push the current branch.

## Output

Provide a concise report with:
- PR number and URL.
- Count summary: total comments, addressed, not applicable, deferred.
- Per-comment disposition (comment URL + decision + short rationale).
- Files changed.
- Compile/test commands run and their final status.
- Commit hash and pushed branch.

## Notes

- Use `tools/get_branch_pr_comments.py` as the default retrieval path; it wraps `tools/get_pr_comments.py`.
- If retrieval needs customization (repo/token/review-body filtering), pass through the corresponding flags supported by `tools/get_branch_pr_comments.py`.
- Do not claim completion if compile/tests are still failing.
