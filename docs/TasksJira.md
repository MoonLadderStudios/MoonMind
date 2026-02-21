# Tasks Jira

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-21  
Related: `docs/TaskArchitecture.md`, `docs/TaskQueueSystem.md`, `docs/SpecKitAutomation.md`

---

## 1. Purpose

Define a declarative, composable Jira-driven task workflow for MoonMind using Jira Cloud as the source system.

The workflow is intentionally split into small skills:

1. `jira-fetch` for deterministic Jira ingestion and normalization.
2. A pluggable implementation skill (for example, Speckit workflows).
3. `jira-pr` for deterministic branch/PR operations.

---

## 2. Jira Cloud Scope

This design targets Jira Cloud only:

1. Issue API endpoint: `/rest/api/3/issue/{ISSUE_KEY}`.
2. Description payload format: Atlassian Document Format (ADF), with optional rendered HTML expansion.
3. Authentication via MoonMind Atlassian settings (`ATLASSIAN_URL`, `ATLASSIAN_USERNAME`, `ATLASSIAN_API_KEY`).

Out of scope:

1. Jira Data Center endpoint compatibility (`/rest/api/2`) and custom auth plugins.

---

## 3. Skill Types (Declarative Split)

### 3.1 Ingestion Skill (Deterministic)

Skill ID: `jira-fetch`

Responsibility:

1. Read one Jira issue key.
2. Fetch Jira Cloud issue data.
3. Normalize issue data into stable artifacts.
4. Derive branch/PR metadata without LLM inference.

Guarantee:

1. Produces a complete artifact set under a deterministic path.
2. Does not modify repository code.

### 3.2 Solver Skill (Pluggable Implementation)

Skill IDs: one of `speckit-*` or any approved implementation skill.

Responsibility:

1. Consume normalized Jira artifacts.
2. Plan and implement code changes.
3. Commit changes to the active Jira branch.

Guarantee:

1. Must treat Jira artifacts as source-of-truth input.
2. Must not rewrite generated Jira metadata unless explicitly requested.

### 3.3 Delivery Skill (Deterministic)

Skill ID: `jira-pr`

Responsibility:

1. Create/check out/push the Jira-derived branch.
2. Create a pull request from derived PR metadata.

Guarantee:

1. Uses `context.json` values directly.
2. Refuses PR creation when branch has no commits ahead of base.

---

## 4. Artifact Contract (Required Handoff)

For issue `ABC-123`, `jira-fetch` MUST write artifacts under the configured workflow artifact root (for example `settings.spec_workflow.artifacts_root`) using a Jira namespace:

1. `<artifacts_root>/jira/ABC-123/issue.raw.json`
2. `<artifacts_root>/jira/ABC-123/issue.normalized.json`
3. `<artifacts_root>/jira/ABC-123/issue.md`
4. `<artifacts_root>/jira/ABC-123/context.json`

`context.json` required fields:

```json
{
  "issueKey": "ABC-123",
  "projectKey": "ABC",
  "issueNumber": "123",
  "jiraUrl": "https://<tenant>.atlassian.net/browse/ABC-123",
  "issueType": "Bug",
  "category": "bugfix",
  "summary": "Fix null pointer on login",
  "slug": "fix-null-pointer-on-login",
  "branchName": "bugfix/ABC-123-fix-null-pointer-on-login",
  "prTitle": "ABC-123: Fix null pointer on login",
  "prBody": "Resolves [ABC-123](https://<tenant>.atlassian.net/browse/ABC-123).\n\n## Summary\n\nDetailed summary of changes, often derived from the Jira issue description."
}
```

Downstream skills may only depend on this contract, not hidden in-memory state.

---

## 5. Declarative Branch Naming Rules

Branch format:

1. `<category>/<jira project prefix>-<jira story ID>-<description>`

Rules:

1. `category`:
   `bugfix` when issue type is `Bug`; otherwise `feature` for Story/Task/default.
2. `jira project prefix`: from issue key (for example `ABC` in `ABC-123`).
3. `jira story ID`: numeric part (for example `123` in `ABC-123`).
4. `description`: slug from issue summary.

Slug normalization:

1. Lowercase.
2. Replace non `[a-z0-9]` with `-`.
3. Collapse repeated `-`.
4. Trim leading/trailing `-`.
5. Fallback to `no-description` if empty.
6. Recommended max length: 50 characters.

Example:

1. `bugfix/ABC-123-fix-null-pointer-on-login`

---

## 6. Skill Contracts

### 6.1 `jira-fetch`

Inputs:

1. Issue key (`ABC-123`).
2. Env config: `ATLASSIAN_URL`, `ATLASSIAN_USERNAME`, and `ATLASSIAN_API_KEY`.

Outputs:

1. Required four-file artifact contract.

Execution rule:

1. Deterministic script-first execution (`scripts/jira_fetch.py`), no implementation side effects.

### 6.2 Implementation Skill

Inputs:

1. `issue.md`, `issue.normalized.json`, and optionally `context.json`.

Outputs:

1. Code, tests, and git commits on the active Jira branch.

Execution rule:

1. Pluggable, but must preserve compatibility with the artifact contract.

### 6.3 `jira-pr`

Inputs:

1. `--issue ABC-123` (resolves `context.json`) or `--context <path>`.
2. Optional `--mode prepare|submit`, `--base main`, `--remote origin`.

Outputs:

1. `prepare`: branch created/checked out and pushed upstream.
2. `submit`: branch pushed and PR created if none exists.

Execution rule:

1. Deterministic script-first execution (`scripts/jira_pr.py`) with explicit fail-fast checks.

---

## 7. Declarative Pipeline Shape

Canonical sequence:

1. `jira-fetch(issue=ABC-123)`
2. `jira-pr(issue=ABC-123, mode=prepare, base=main)`
3. `implementation-skill(issue=ABC-123)`
4. `jira-pr(issue=ABC-123, mode=submit, base=main)`

Example MoonMind job payloads:

```json
{
  "type": "task",
  "payload": {
    "skillId": "jira-fetch",
    "inputs": {
      "issue": "ABC-123"
    }
  }
}
```

```json
{
  "type": "task",
  "payload": {
    "skillId": "jira-pr",
    "inputs": {
      "issue": "ABC-123",
      "mode": "submit",
      "base": "main"
    }
  }
}
```

---

## 8. Policy and Safety Defaults

1. Treat `jira-fetch` and `jira-pr` as deterministic operation skills.
2. Require explicit invocation for PR-capable skills.
3. Restrict skill execution with allowlist policy in sensitive environments.
4. Never persist credentials in artifacts, logs, or PR body.
5. Require `context.json` for branch and PR metadata source-of-truth.

---

## 9. Acceptance Criteria

1. A Jira Cloud issue can be fetched into the required artifact contract.
2. Branch naming always matches the declared format and category mapping.
3. Implementation stage can be swapped without changing upstream/downstream skills.
4. PR creation is reproducible from `context.json` and fails safely when branch is not ahead of base.
5. No hidden state is required across stages; file artifacts are sufficient.
