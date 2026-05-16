---
name: jira-verify
description: >-
  Verify a Jira issue against the current repository state, then post a Jira
  comment with a PASS, PARTIAL, FAIL, or BLOCKED verdict. Works in two modes:
  (a) feature-branch mode, comparing a checked-out branch to its base ref; or
  (b) main/trunk mode, verifying that an issue is already implemented on the
  current default branch (typical for stories that have already merged). Use
  when a user asks whether a branch or merged change completes a Jira ticket,
  or needs a Jira-visible verification comment.
---

# Jira Verify

Verify whether the repository satisfies a Jira issue, then publish a concise Jira comment with the result. This skill supports two equally valid verification modes:

- **Branch mode** — a feature branch is checked out, distinct from the base ref; verification compares the diff `<base>..HEAD` to the Jira requirements.
- **Main/trunk mode** — the current checkout is the default branch itself (e.g. `main` at `origin/main`), or there is no meaningful diff against the base ref. In this mode, verify that the issue is already implemented in the codebase as it stands. This is the expected mode when the work has already merged and the user just wants confirmation that the story landed.

Choose the mode automatically based on observed repository state. Do not treat "no feature branch / no diff vs. base" as an immediate `BLOCKED` — fall back to main/trunk verification first, and only block if even main/trunk evidence is missing.

## Inputs

- Required: Jira issue key or URL, for example `ENG-123`.
- Required: current repository checkout containing the branch to verify.
- Required for Jira content and posting: MoonMind's trusted Jira tool surface, normally `jira.get_issue` and `jira.add_comment`.
- Optional: update status, boolean, default `false`. When `true`, and only when the final verification verdict is `PASS`, the skill may move the Jira issue to a terminal done state through MoonMind's trusted Jira tool surface.
- Optional: transition ID, transition name, or transition fields. Use these to disambiguate or satisfy required fields when moving the issue to a terminal state.
- Optional: base branch or comparison ref. If omitted, infer from upstream, `origin/main`, `origin/master`, `main`, or `master`. When the checkout is already on the default branch or the diff against the inferred base is empty, switch to main/trunk verification (see Workflow) instead of blocking.
- Optional: explicit verification mode hint (`branch`, `main`, or `auto`). Default `auto`.
- Optional: history search window (commit count or date range) for locating prior merges that implement the issue on the default branch. Default: scan the last ~200 commits and any commits in the last ~90 days.
- Optional: required test commands, scope limits, or explicit non-goals.

## MoonMind Jira Access Model

Do not expect raw Jira credentials inside the managed agent shell. MoonMind keeps Atlassian credentials on the trusted control-plane/tool side. Use Jira artifacts or trusted Jira tool output as the source of truth. Use `jira.add_comment` for comment mutation, and use `jira.get_transitions` plus `jira.transition_issue` for status mutation when `update status` is explicitly true.

If Jira content is not already available to the runtime, use the trusted MCP path when exposed:

1. List tools with `GET $MOONMIND_URL/mcp/tools`.
2. Verify Jira authentication with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.verify_connection","arguments":{}}`.
3. Fetch the issue with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.get_issue","arguments":{"issueKey":"ENG-123"}}`.
4. If `update status` is true, fetch available transitions with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.get_transitions","arguments":{"issueKey":"ENG-123"}}`, then transition only through `jira.transition_issue` with JSON `{"tool":"jira.transition_issue","arguments":{"issueKey":"ENG-123","transitionId":"101","fields":{}}}` after the PASS-only checks below succeed.

If `jira.verify_connection` reports `jira_auth_failed`, or `jira.get_issue` / `jira.add_comment` is unavailable or policy-denied, report `BLOCKED`. If `update status` is true and transition tools are unavailable or policy-denied, leave the verification/comment path intact but report status update as skipped/blocked in the outputs and Jira comment. Do not scrape private Atlassian browser pages, ask for `ATLASSIAN_API_KEY`, or call Jira directly with raw credentials.

Never print raw environment variables. Use targeted checks such as `test -n "$MOONMIND_URL"`; do not run `printenv`, `env`, `set`, or equivalent commands that can dump secrets into logs.

## Workflow

1. Resolve the Jira issue.
   - Normalize the issue key from a key or URL.
   - Load trusted issue content from existing artifacts such as `var/artifacts/**/jira/<ISSUE>/issue.normalized.json` or `issue.md` when present.
   - Otherwise fetch through trusted Jira tooling.
   - Extract a requirements ledger: summary, description goals, acceptance criteria, constraints, explicit non-goals, linked issue dependencies, and any test or deployment expectations.
   - If requirements are ambiguous, mark them `unverifiable` instead of inventing criteria.

2. Choose a verification mode and resolve the comparison.
   - Record `git branch --show-current`, `git rev-parse HEAD`, and `git status --short`.
   - Determine the candidate base ref from user input, upstream tracking branch, `origin/main`, `origin/master`, `main`, or `master`. Fetch the base ref only when needed and safe.
   - Decide the mode:
     - **Branch mode** when the current checkout is NOT the default branch AND `git rev-list --count <base>..HEAD` is greater than 0.
     - **Main/trunk mode** when the current checkout IS the default branch, when HEAD already equals the base ref, when `git rev-list --count <base>..HEAD` is `0`, or when the user explicitly requested `main` mode.
     - If a user-supplied mode hint is provided, honor it unless it is impossible (e.g. branch mode requested but no distinct branch exists — then block with a clear reason).
   - For branch mode: use `git merge-base <base> HEAD`, then inspect `git diff --stat <merge-base>..HEAD`, `git diff --name-status <merge-base>..HEAD`, and relevant hunks as the primary evidence set.
   - For main/trunk mode: do NOT block on the empty diff. The repository state at HEAD is itself the evidence. Additionally locate the merge(s) that implemented the issue:
     - `git log -i --grep '<ISSUE-KEY>' --oneline -n 200` to find commits that reference the key in the message.
     - `git log --oneline -n 200 -- <likely paths>` for paths matched by issue keywords when no key reference is found.
     - `gh pr list --search '<ISSUE-KEY>' --state merged --limit 20` when `gh` is authenticated, to locate the merged PR(s) for the issue.
     - For each candidate merge commit, inspect `git show --stat <sha>` and `git diff <sha>^..<sha>` to extract the implementation diff, and treat that as the diff under verification.
     - If multiple merges plausibly implement parts of the issue, aggregate them and note each in the evidence ledger.
   - Branch-mode reminder: if no meaningful diff exists against base AND the checkout is not the default branch, fall back to main/trunk mode rather than declaring `BLOCKED` — the work may already have merged. Only mark `BLOCKED` after both modes fail to yield evidence.

3. Inspect implementation evidence.
   - In branch mode: read changed source, tests, docs, workflow/config, migrations, and generated artifacts within `<merge-base>..HEAD`.
   - In main/trunk mode: read the current state of files relevant to the Jira ledger AND, when available, the implementing merge commit(s) diffs identified above. The "current state on main" is acceptable evidence on its own when it clearly satisfies a requirement; the historical diff is supplementary.
   - In both modes: search the repository with `rg -i` for Jira terms, feature names, acceptance criteria keywords, old behavior, and new behavior. In main/trunk mode, also `rg -i -w` the issue key itself (`MM-555`, etc.) across source, tests, specs, docs, changelog, and `specs/<feature>/` folders.
   - Identify deleted or superseded paths so the verdict accounts for removals as well as additions.
   - Run local tests when required by repo instructions, user request, or when the verdict depends on unproven behavior. If tests cannot run, record exactly why.
   - In main/trunk mode, if no implementing commits, no issue-key references, and no code matching the requirements can be found, then — and only then — record the verdict as `FAIL` (not implemented on main) or `BLOCKED` (requirements too ambiguous to tell), with a clear distinction between the two.

4. Build a traceability ledger before commenting.
   - For each Jira item, assign exactly one status:
     - `met`
     - `partially_met`
     - `not_met`
     - `out_of_scope`
     - `unverifiable`
   - Include evidence for every non-`unverifiable` item: changed file, test, command output summary, commit, or repo search result.
   - Keep non-repo requirements separate from branch-verifiable requirements.

5. Decide the overall result.
   - `PASS`: all in-scope Jira requirements are `met`, whether evidence comes from the branch diff (branch mode) or from the current state of the default branch and prior merge(s) (main/trunk mode).
   - `PARTIAL`: at least one in-scope item is `partially_met` or `unverifiable`, but no clear in-scope miss exists.
   - `FAIL`: at least one in-scope item is `not_met` — including the main/trunk case where no implementing change can be found and the requirements are concrete enough to expect one.
   - `BLOCKED`: trusted Jira content or Jira comment access is unavailable, OR both branch mode and main/trunk mode failed to produce any usable evidence and the requirements are too ambiguous to assess from repository state alone. Simply being on `main` with a clean tree is NOT, by itself, a `BLOCKED` condition.

6. If `update status` is true, decide whether to update Jira status.
   - Do not attempt any status update unless the overall verification result is `PASS`.
   - Treat an issue that is already in a done-category status as already done; record that no transition was needed.
   - Fetch available transitions through the trusted Jira tool surface.
   - Select a completion transition: if a specific transition ID or name was provided, use it if available; otherwise, select a completion transition only when exactly one available transition targets a Jira done-category status.
   - If zero or multiple done-category transitions are available, do not guess. Record the status update as blocked and leave the issue unchanged.
   - If the transition requires fields that were not explicitly provided by the trusted tool input or operator context, do not guess values. Record the status update as blocked and leave the issue unchanged.
   - Execute the selected transition only through `jira.transition_issue`, then record selected transition ID/name, whether the issue was already done, and whether the transition succeeded.
   - If transition execution fails, keep the verification verdict as decided above but report the status update failure separately. Do not claim the issue was moved.

7. Draft the Jira comment.
   - Start with the verdict, issue key, the verification mode used (`branch` or `main/trunk`), branch name, commit SHA, and comparison ref (or "verified against current state of default branch" for main/trunk mode).
   - In main/trunk mode, list the merge commit SHA(s) or merged PR number(s) identified as the implementation evidence, when known.
   - Include blockers or gaps first for `PARTIAL`, `FAIL`, or `BLOCKED`.
   - Include a compact coverage table and evidence references.
   - Include validation observed, clearly separating passing tests from tests not run.
   - Include status update outcome when `update status` is true: skipped because verdict was not `PASS`, already done, transitioned with selected transition, or blocked/failed with sanitized reason.
   - Do not paste long private Jira text, raw command dumps, credentials, auth headers, cookies, or full environment/config dumps.

Suggested comment shape (branch mode):

```markdown
Branch verification for `<ISSUE>`: **<PASS|PARTIAL|FAIL|BLOCKED>**

Mode: branch
Branch: `<branch>` at `<short-sha>`
Compared against: `<base-ref>`

| Jira item | Status | Evidence |
| --- | --- | --- |
| <goal / AC summary> | met | `<file>` / `<test>` |

Gaps / blockers:
- <only when applicable>

Validation:
- Tests run: `<command>` -> `<result>`
- Tests not run: <reason>

Status update:
- <omitted when `update status` is false; otherwise already done / transitioned / skipped / blocked>
```

Suggested comment shape (main/trunk mode, work already merged):

```markdown
Implementation verification for `<ISSUE>`: **<PASS|PARTIAL|FAIL|BLOCKED>**

Mode: main/trunk
Default branch: `<branch>` at `<short-sha>`
Implementing change(s): `<merge-sha>` (PR #<num>), `<merge-sha-2>` (PR #<num-2>)

| Jira item | Status | Evidence |
| --- | --- | --- |
| <goal / AC summary> | met | `<file:line>` at `<sha>` / `<test>` |

Gaps / blockers:
- <only when applicable>

Validation:
- Tests run: `<command>` -> `<result>`
- Tests not run: <reason>

Status update:
- <omitted when `update status` is false; otherwise already done / transitioned / skipped / blocked>
```

8. Scan and post to Jira.
   - Before posting, scan the outgoing comment for secret-like patterns such as `ghp_`, `github_pat_`, `ATATT`, `AIza`, `AKIA`, private key blocks, `token=`, `password=`, and `Authorization:`.
   - If any secret-like content appears, do not post. Redact and re-scan.
   - If the bundled helper is materialized, post with:

```bash
.agents/skills/jira-verify/tools/post_jira_comment.py --issue <ISSUE> --body-file <comment_file>
```

   - When the MoonMind API requires auth, provide an existing runtime token via `MOONMIND_AUTH_HEADER`, `MOONMIND_API_TOKEN`, `MOONMIND_AUTH_TOKEN`, `MOONMIND_BEARER_TOKEN`, or `MOONMIND_API_KEY`; do not print those values.
   - Otherwise call the trusted Jira tool directly with `jira.add_comment` and arguments `{"issueKey":"<ISSUE>","body":"<comment text>"}`.
   - If posting fails, keep the comment body artifact and report the exact trusted-tool blocker. Do not claim Jira was updated.

## Outputs

- Jira comment result or URL/ID when posting succeeds.
- Verification ledger path, preferably `var/jira_verify/<ISSUE>-<branch>.json`. Include the verification mode (`branch` or `main/trunk`) and, in main/trunk mode, the implementing merge SHA(s)/PR number(s) when identified.
- Comment body path, preferably `var/jira_verify/<ISSUE>-<branch>.md`.
- Status update result: `not_requested` when `update status` is false; otherwise `skipped`, `already_done`, `transitioned`, `blocked`, or `failed`, including selected transition evidence when applicable.
- Final status: `PASS`, `PARTIAL`, `FAIL`, or `BLOCKED`.

## Failure Modes

- Missing trusted Jira content: `BLOCKED`; request a trusted Jira fetch/import or working `jira.get_issue` tool.
- Jira issue inaccessible: `BLOCKED`; include issue key and sanitized tool error.
- Branch comparison unavailable AND main/trunk mode also yields no usable evidence: `BLOCKED`; identify the missing base ref, missing history, or unsearchable repository state.
- Current checkout is the default branch with a clean tree: do NOT block. Run main/trunk verification — search the repository state and recent merge history for the issue. Block only if that also produces no evidence and the requirements are too ambiguous to assess.
- Default-branch checkout with no diff and no Jira-keyed merge found: prefer `FAIL` ("not implemented on `<default-branch>`") over `BLOCKED` when the Jira requirements are concrete enough to expect a code change. Reserve `BLOCKED` for ambiguous requirements.
- Tests unavailable: continue only if evidence is otherwise sufficient; otherwise mark affected items `unverifiable`.
- Jira comment cannot be posted: return the draft comment artifact and the sanitized posting error.
- Jira status update cannot be attempted safely: do not transition the issue; report status update as `blocked` or `failed` separately from the verification verdict.
- Verification verdict is not `PASS`: do not transition the issue even when `update status` is true; report status update as `skipped`.
- Requirements ambiguous: mark affected items `unverifiable`; do not treat them as passing.
