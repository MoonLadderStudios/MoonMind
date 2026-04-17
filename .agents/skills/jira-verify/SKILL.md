---
name: jira-verify
description: Verify the current repository branch against a Jira issue's goals, requirements, and acceptance criteria, then post a Jira comment with a PASS, PARTIAL, FAIL, or BLOCKED verdict. Use when a user asks whether the current branch completes a Jira ticket, wants Jira issue completion checked from local branch changes, or needs a Jira-visible verification comment.
---

# Jira Verify

Verify whether the current checkout/branch satisfies a Jira issue, then publish a concise Jira comment with the result.

## Inputs

- Required: Jira issue key or URL, for example `ENG-123`.
- Required: current repository checkout containing the branch to verify.
- Required for Jira content and posting: MoonMind's trusted Jira tool surface, normally `jira.get_issue` and `jira.add_comment`.
- Optional: base branch or comparison ref. If omitted, infer from upstream, `origin/main`, `origin/master`, `main`, or `master`.
- Optional: required test commands, scope limits, or explicit non-goals.

## MoonMind Jira Access Model

Do not expect raw Jira credentials inside the managed agent shell. MoonMind keeps Atlassian credentials on the trusted control-plane/tool side. Use Jira artifacts or trusted Jira tool output as the source of truth and use `jira.add_comment` for mutation.

If Jira content is not already available to the runtime, use the trusted MCP path when exposed:

1. List tools with `GET $MOONMIND_URL/mcp/tools`.
2. Verify Jira authentication with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.verify_connection","arguments":{}}`.
3. Fetch the issue with `POST $MOONMIND_URL/mcp/tools/call` and JSON `{"tool":"jira.get_issue","arguments":{"issueKey":"ENG-123"}}`.

If `jira.verify_connection` reports `jira_auth_failed`, or `jira.get_issue` / `jira.add_comment` is unavailable or policy-denied, report `BLOCKED`. Do not scrape private Atlassian browser pages, ask for `ATLASSIAN_API_KEY`, or call Jira directly with raw credentials.

Never print raw environment variables. Use targeted checks such as `test -n "$MOONMIND_URL"`; do not run `printenv`, `env`, `set`, or equivalent commands that can dump secrets into logs.

## Workflow

1. Resolve the Jira issue.
- Normalize the issue key from a key or URL.
- Load trusted issue content from existing artifacts such as `var/artifacts/**/jira/<ISSUE>/issue.normalized.json` or `issue.md` when present.
- Otherwise fetch through trusted Jira tooling.
- Extract a requirements ledger: summary, description goals, acceptance criteria, constraints, explicit non-goals, linked issue dependencies, and any test or deployment expectations.
- If requirements are ambiguous, mark them `unverifiable` instead of inventing criteria.

2. Resolve the branch comparison.
- Record `git branch --show-current`, `git rev-parse HEAD`, and `git status --short`.
- Determine the comparison ref from user input, upstream tracking branch, `origin/main`, `origin/master`, `main`, or `master`.
- Fetch the comparison ref only when needed and safe.
- Use `git merge-base <base> HEAD`, then inspect `git diff --stat <merge-base>..HEAD`, `git diff --name-status <merge-base>..HEAD`, and relevant hunks.
- If no meaningful diff exists, the likely verdict is `FAIL` or `BLOCKED` unless the Jira issue explicitly requires only verification/no code change.

3. Inspect implementation evidence.
- Read changed source, tests, docs, workflow/config, migrations, and generated artifacts relevant to the Jira ledger.
- Search the repository with `rg` for Jira terms, feature names, acceptance criteria keywords, old behavior, and new behavior.
- Identify deleted or superseded paths so the verdict accounts for removals as well as additions.
- Run local tests when required by repo instructions, user request, or when the verdict depends on unproven behavior. If tests cannot run, record exactly why.

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
- `PASS`: all in-scope, branch-verifiable Jira requirements are `met`.
- `PARTIAL`: at least one in-scope item is `partially_met` or `unverifiable`, but no clear in-scope miss exists.
- `FAIL`: at least one in-scope item is `not_met`.
- `BLOCKED`: trusted Jira content, branch comparison, or Jira comment access is unavailable.

6. Draft the Jira comment.
- Start with the verdict, issue key, branch name, commit SHA, and comparison ref.
- Include blockers or gaps first for `PARTIAL`, `FAIL`, or `BLOCKED`.
- Include a compact coverage table and evidence references.
- Include validation observed, clearly separating passing tests from tests not run.
- Do not paste long private Jira text, raw command dumps, credentials, auth headers, cookies, or full environment/config dumps.

Suggested comment shape:

```markdown
Branch verification for `<ISSUE>`: **<PASS|PARTIAL|FAIL|BLOCKED>**

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
```

7. Scan and post to Jira.
- Before posting, scan the outgoing comment for secret-like patterns such as `ghp_`, `github_pat_`, `ATATT`, `AIza`, `AKIA`, private key blocks, `token=`, `password=`, and `Authorization:`.
- If any secret-like content appears, do not post. Redact and re-scan.
- If the bundled helper is materialized, post with:

```bash
.agents/skills/jira-verify/tools/post_jira_comment.py --issue <ISSUE> --body-file <comment_file>
```

- Otherwise call the trusted Jira tool directly with `jira.add_comment` and arguments `{"issueKey":"<ISSUE>","body":"<comment text>"}`.
- If posting fails, keep the comment body artifact and report the exact trusted-tool blocker. Do not claim Jira was updated.

## Outputs

- Jira comment result or URL/ID when posting succeeds.
- Verification ledger path, preferably `var/jira_verify/<ISSUE>-<branch>.json`.
- Comment body path, preferably `var/jira_verify/<ISSUE>-<branch>.md`.
- Final status: `PASS`, `PARTIAL`, `FAIL`, or `BLOCKED`.

## Failure Modes

- Missing trusted Jira content: `BLOCKED`; request a trusted Jira fetch/import or working `jira.get_issue` tool.
- Jira issue inaccessible: `BLOCKED`; include issue key and sanitized tool error.
- Branch comparison unavailable: `BLOCKED`; identify the missing base ref or repository state.
- Tests unavailable: continue only if evidence is otherwise sufficient; otherwise mark affected items `unverifiable`.
- Jira comment cannot be posted: return the draft comment artifact and the sanitized posting error.
- Requirements ambiguous: mark affected items `unverifiable`; do not treat them as passing.
