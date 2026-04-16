---
name: jira-pr-verify
description: Verify a GitHub pull request against a Jira issue's goals, requirements, and acceptance criteria, then post a PR comment with the findings. Use when a user asks Codex or MoonMind to compare a PR to a Jira story/task/bug, confirm whether a PR satisfies Jira requirements, audit implementation coverage from Jira, or publish a Jira-vs-PR verification summary.
---

# Jira PR Verify

Verify whether a PR satisfies the linked Jira issue and publish a concise PR comment with evidence-backed findings.

## Inputs

- Required: Jira issue key or URL.
- Required: PR URL or PR number plus repository.
- Required: trusted Jira issue content, available through one of:
  - existing `jira-fetch` artifacts such as `<artifact_root>/jira/<ISSUE>/issue.normalized.json`, `issue.md`, and `context.json`,
  - a MoonMind trusted Jira tool/connector response attached to the run or fetched during the run through `jira.get_issue`,
  - user-supplied Jira text pasted into the task.
- Required for posting: authenticated GitHub access through `gh` or the GitHub app/connector.
- Optional: repo-only scope limits, out-of-scope areas, required test commands, or comment style.

## MoonMind Access Model

Do not expect raw Jira credentials inside the managed agent shell. MoonMind intentionally keeps `ATLASSIAN_*` secrets on the trusted control-plane/tool side. Use Jira artifacts or trusted Jira tool output as the source of truth.

If Jira content is not already available to the managed agent, first use MoonMind's trusted Jira MCP tool path when it is exposed to the runtime:

1. List tools with `GET $MOONMIND_URL/mcp/tools`.
2. Fetch the issue with `POST $MOONMIND_URL/mcp/tools/call` and JSON like `{"tool":"jira.get_issue","arguments":{"issueKey":"KANDY-2558"}}`.
3. Use the sanitized tool result as the Jira source of truth.

If `jira.get_issue` is unavailable, denied by policy, or the runtime does not expose `$MOONMIND_URL`, stop as blocked and report that the task must run a trusted Jira fetch/import step first. Do not scrape a private Atlassian browser page, infer hidden acceptance criteria from a branch name, or ask for `ATLASSIAN_API_KEY` in the agent environment.

For MoonMind task plans, the correct shape is:

1. Trusted Jira fetch/import step materializes normalized Jira issue artifacts.
2. Managed agent step uses this skill with those artifacts plus the PR URL.
3. Managed agent uses `gh`/GitHub connector to inspect and comment on the PR.

## Workflow

1. Resolve targets.
- Normalize the Jira key from the issue URL/key.
- Resolve the PR number, repository, base branch, and head branch.
- If the local checkout is not the PR head, inspect it carefully and check out/fetch the PR only when needed.

2. Load Jira requirements.
- Prefer `issue.normalized.json` and `issue.md` from the trusted Jira artifact directory.
- If no Jira artifact is present and `$MOONMIND_URL` is available, call the trusted MCP tool `jira.get_issue` for the issue key.
- Extract a ledger of goals, functional requirements, acceptance criteria, constraints, explicit non-goals, and referenced docs.
- Preserve Jira wording in summaries, but do not paste long private Jira text into the PR comment.
- If acceptance criteria are missing or ambiguous, record them as `unverifiable` rather than inventing requirements.

3. Inspect the PR.
- Use `gh pr view <pr> --repo <owner/repo> --json number,url,title,body,baseRefName,headRefName,files,commits,statusCheckRollup,reviewDecision,comments,reviews` when available.
- Use `gh pr diff <pr> --repo <owner/repo>` or local `git diff <base>...HEAD` for implementation evidence.
- Read changed code, tests, docs, workflow files, and configuration relevant to the Jira ledger.
- Check CI status with `gh pr checks <pr> --repo <owner/repo>` when available.
- Run local tests only when the user asks, the repo instructions require it for this verification, or the implementation claim depends on local validation.

4. Build a traceability ledger before commenting.
- For each Jira goal/requirement/acceptance criterion, assign one status:
  - `met`
  - `partially_met`
  - `not_met`
  - `out_of_scope`
  - `unverifiable`
- Include evidence pointers for every non-`unverifiable` item: changed file, test, commit, CI check, or PR discussion.
- Keep repo-bound and non-repo-bound requirements separate when the user scopes verification to one repository.

5. Decide the overall result.
- `PASS`: all in-scope Jira requirements are `met`; only explicit non-repo/out-of-scope items are excluded.
- `PARTIAL`: at least one in-scope item is `partially_met` or `unverifiable`, but no clear in-scope miss exists.
- `FAIL`: at least one in-scope item is `not_met`.
- `BLOCKED`: trusted Jira content, PR access, or GitHub comment access is unavailable.

6. Draft the PR comment.
- Start with the overall result and Jira/PR identifiers.
- Include a compact coverage table.
- Include blockers/gaps first when the result is `PARTIAL`, `FAIL`, or `BLOCKED`.
- Include tests/CI observed, and clearly separate "not run" from "passing".
- Include out-of-scope Jira items only when they affected the verdict.
- Do not include raw credentials, auth headers, cookies, full environment dumps, or large Jira excerpts.

Suggested comment shape:

```markdown
Jira verification for `<ISSUE>` against this PR: **<PASS|PARTIAL|FAIL|BLOCKED>**

| Jira item | Status | Evidence |
| --- | --- | --- |
| <goal / AC summary> | met | `<file>` / `<test>` / CI check |

Notes:
- <gap, blocker, or scope note>

Validation:
- CI: <checks observed>
- Local tests: <commands run or not run>
```

7. Scan and post.
- Before posting, scan the outgoing comment for secret-like patterns such as `ghp_`, `github_pat_`, `ATATT`, `AIza`, `AKIA`, private key blocks, `token=`, `password=`, and `Authorization:`.
- If any secret-like content appears, do not post. Redact and re-scan.
- Post with `gh pr comment <pr> --repo <owner/repo> --body-file <comment_file>` or the GitHub connector.
- If posting fails, leave the comment body in a local artifact and report the exact GitHub blocker.

## Outputs

- PR comment URL when posting succeeds.
- Verification ledger path, preferably `var/jira_pr_verify/<ISSUE>-pr-<PR>.json`.
- Comment body path, preferably `var/jira_pr_verify/<ISSUE>-pr-<PR>.md`.
- Final status: `PASS`, `PARTIAL`, `FAIL`, or `BLOCKED`.

## Failure Modes

- Missing trusted Jira content: block and request a prior MoonMind Jira fetch/import step.
- Jira issue is inaccessible through trusted tooling: block and identify the issue key and missing capability.
- GitHub PR is inaccessible: block with the repo/PR and the `gh` or connector error.
- PR comment cannot be posted: return the draft comment artifact and the posting error.
- Jira requirements are ambiguous: mark affected items `unverifiable`; do not treat them as passing.
