# Feature Specification: GitHub Token Permission Improvements

**Feature Branch**: `294-github-token-permissions`
**Created**: 2026-05-04
**Status**: Draft
**Input**: User description:

````text
Implement the recommended token improvements:

Based on the public `main` branch, I would treat this as a **token wiring + fine-grained permission profile** problem, not primarily as “MoonMind rejects `github_pat_` tokens.”

MoonMind already recognizes `github_pat_` as secret-like in the settings catalog and publish sanitization, and `GitHubService` already sends REST API auth as `Authorization: Bearer ...`, which is fine for fine-grained PATs. The bigger issue is that some code paths still depend on **ambient `git` / `gh` credentials**, while fine-grained PATs only work when the right token is explicitly passed to the exact repo/API operation with the exact permissions. ([GitHub][1])

## Necessary project changes

### 1. Unify GitHub token resolution across the project

Right now there are at least two different token concepts:

`api_service/services/settings_catalog.py` exposes `integrations.github.token_ref` with env alias `MOONMIND_GITHUB_TOKEN_REF`, but `GitHubService.resolve_github_token()` only checks an explicit token, `GITHUB_TOKEN`, or `settings.github.github_token_secret_ref`. Separately, `GitHubSettings.github_token_secret_ref` only maps `GITHUB_TOKEN_SECRET_REF` and `WORKFLOW_GITHUB_TOKEN_SECRET_REF`, not `MOONMIND_GITHUB_TOKEN_REF`. That means a token reference configured through the Settings catalog can easily fail to reach the runtime code that actually calls GitHub. ([GitHub][1])

Change this by adding one canonical resolver, then use it everywhere:

```python
async def resolve_github_credential(
    explicit_token: str | None = None,
    *,
    repo: str | None = None,
) -> str:
    # precedence:
    # 1. explicit token
    # 2. GITHUB_TOKEN / GH_TOKEN / WORKFLOW_GITHUB_TOKEN
    # 3. GITHUB_TOKEN_SECRET_REF / WORKFLOW_GITHUB_TOKEN_SECRET_REF
    # 4. MOONMIND_GITHUB_TOKEN_REF / integrations.github.token_ref
    ...
```

Then call that resolver from:

`GitHubService`

`GitHubIndexer`

`PublishService`

any worker/container materialization code that performs `git clone`, `git fetch`, `git push`, or `gh pr create`

At minimum, add `MOONMIND_GITHUB_TOKEN_REF` as an alias for `settings.github.github_token_secret_ref`, and consider accepting `WORKFLOW_GITHUB_TOKEN` as a direct token source too.

### 2. Stop relying on ambient `git` and `gh` authentication in `PublishService`

This is likely the main reason classic tokens appear to work while fine-grained tokens do not. `PublishService.publish()` currently runs:

```text
git push -u origin <branch>
gh pr create ...
```

without passing a resolved token into either command. If the machine already has a classic token cached in Git Credential Manager or `gh auth`, publishing can work. But a fine-grained PAT saved in MoonMind settings is not automatically used by `git push` or `gh pr create`. ([GitHub][2])

For `gh`, set `GH_TOKEN` and `GITHUB_TOKEN` in the subprocess environment. The GitHub CLI docs say `GH_TOKEN` and then `GITHUB_TOKEN` are used for `github.com`, and the `gh auth login` docs specifically recommend `GH_TOKEN` for fine-grained PAT usage in automation. ([GitHub CLI][3])

For `git push`, make the push token-aware. GitHub’s docs say PATs are used as the HTTPS password for Git operations, and PATs only work over HTTPS remotes, not SSH remotes. ([GitHub Docs][4])

A safe implementation shape:

```python
token = await resolve_github_credential(repo=repo)

env = {
    **os.environ,
    "GITHUB_TOKEN": token,
    "GH_TOKEN": token,
    "GIT_TERMINAL_PROMPT": "0",
}

await run_git_push_with_credential_helper(
    repo_dir=repo_dir,
    branch_name=branch_name,
    token=token,
    redaction_values=(token,),
)

# Prefer REST over gh for deterministic permissions:
await github_service.create_pull_request(
    repo=repo,
    head=branch_name,
    base=base_branch,
    title=pr_title,
    body=pr_body,
    github_token=token,
)
```

I would prefer replacing `gh pr create` with `GitHubService.create_pull_request()` because MoonMind already has a REST PR service, and REST failures can report the exact missing fine-grained permission. If keeping `gh`, pass `GH_TOKEN`, set `GH_REPO` when useful, and redact the token from logs.

### 3. Define and validate fine-grained PAT permission profiles

Classic PATs often work because `repo` is broad. Fine-grained PATs need exact repository permissions per endpoint. GitHub’s docs explicitly say each REST endpoint declares whether it supports fine-grained PATs and which permissions are required. ([GitHub Docs][4])

For **MoonMind repository indexing only**, the minimum profile should be:

Resource owner: `MoonLadderStudios`

Repository access: selected repo `MoonMind`

Repository permissions: `Contents: read`

Metadata read is implicit for fine-grained PATs. MoonMind’s `GitHubIndexer` fetches the default branch and loads repository content, and GitHub’s branch/content endpoints require `Contents: read` for fine-grained PATs. ([GitHub][5])

For **branch publishing + PR creation**, the minimum profile should be:

`Contents: read and write` — needed for pushing commits/branches and merging PRs

`Pull requests: read and write` — needed to create/update PRs

`Workflows: write` — only if MoonMind may modify files under `.github/workflows/*`

GitHub’s PR create/update endpoints require `Pull requests: write`, the merge endpoint requires `Contents: write`, and the contents API notes that modifying `.github/workflows` requires workflow-level permission in addition to content write. ([GitHub Docs][6])

For **PR readiness checks**, MoonMind currently calls these GitHub endpoints in `GitHubService.evaluate_pull_request_readiness()`:

`GET /pulls/{number}`

`GET /commits/{sha}/status`

`GET /commits/{sha}/check-runs`

`GET /pulls/{number}/reviews`

`GET /issues/{number}/reactions`

That means the fine-grained PAT profile also needs `Pull requests: read`, `Commit statuses: read`, `Checks: read`, and `Issues: read` if the reaction fallback is enabled. GitHub’s docs list those exact permissions for the respective endpoints. ([GitHub][7])

A practical “full MoonMind PR automation” fine-grained PAT would therefore be:

```text
Resource owner: MoonLadderStudios
Repository access: Only select repositories -> MoonMind

Repository permissions:
  Contents: Read and write
  Pull requests: Read and write
  Commit statuses: Read
  Checks: Read
  Issues: Read
  Workflows: Write        # only if agents may edit .github/workflows/*
```

### 4. Make readiness checks degrade gracefully when optional permissions are missing

`Issues: read` is a good example. MoonMind’s reaction-based Codex review fallback calls the issue reactions endpoint for a PR, because PRs are also issues in GitHub’s API. A token with `Contents` and `Pull requests` permissions but no `Issues: read` can create a PR but fail readiness evaluation. Classic `repo` hides that because it grants broad repo access. ([GitHub][7])

Change `_evaluate_codex_review_reaction()` so a 403 caused by missing `Issues: read` is reported as “reaction evidence unavailable; grant Issues read or disable reaction fallback,” instead of making fine-grained PATs look globally broken. Do the same for checks: if `Checks: read` is missing, report that specific permission, not just `HTTP 403`.

### 5. Surface GitHub’s permission diagnostics instead of generic HTTP failures

`GitHubService` logs the GitHub response body, but returns generic summaries like “GitHub create PR failed with HTTP 403.” That is especially painful for fine-grained PATs, because GitHub often provides useful missing-permission signals. ([GitHub][7])

Change error handling to return a sanitized diagnostic containing:

HTTP status

GitHub response `message`

`documentation_url`

`X-Accepted-GitHub-Permissions` response header, when present

GitHub added `x-accepted-github-permissions` specifically to show which permissions are required for fine-grained permission actors such as GitHub Apps and fine-grained PATs. ([The GitHub Blog][8])

Example user-facing diagnostic:

```text
GitHub create PR failed: token can access MoonLadderStudios/MoonMind,
but this endpoint requires Pull requests: write.
Current request returned HTTP 403: Resource not accessible by personal access token.
```

### 6. Add a targeted token probe; do not validate fine-grained PATs like classic scopes

Do not use classic-token assumptions such as “does `/user/repos` list everything?” or “does `X-OAuth-Scopes` include `repo`?” Fine-grained PATs are intentionally limited to one resource owner and selected repositories, and they use repository permissions rather than classic OAuth scopes. GitHub says fine-grained PATs are limited to resources owned by a single user or organization, can be limited to selected repositories, and use fine-grained permissions instead of classic scopes. ([GitHub Docs][4])

Add a validation flow that asks for or derives the target `owner/repo`, then probes that exact repository:

```text
GET /repos/MoonLadderStudios/MoonMind
GET /repos/MoonLadderStudios/MoonMind/branches/<base>
GET /repos/MoonLadderStudios/MoonMind/pulls?per_page=1
```

For publish mode, also show a preflight permission checklist: `Contents: write`, `Pull requests: write`, and optionally `Workflows: write`, `Commit statuses: read`, `Checks: read`, `Issues: read`.

### 7. Document cases where a fine-grained PAT still will not work

Some failures are not fixable in MoonMind. The token must be created with the correct **resource owner**. For `MoonLadderStudios/MoonMind`, that means the fine-grained PAT must target `MoonLadderStudios` and include the `MoonMind` repo. If the org requires approval, the token can be pending and will only read public resources until approved. GitHub’s 2025 GA announcement also says fine-grained PATs are enabled by default for orgs unless disabled, and the approval flow is enabled by default. ([GitHub Docs][4])

Fine-grained PATs also still have limitations: they cannot access multiple organizations at once, and outside collaborators can only use classic PATs for organization repositories they collaborate on. For long-lived org automation, GitHub recommends using a GitHub App rather than a personal access token. ([GitHub Docs][4])

## Bottom line

The key MoonMind changes are:

1. **Unify token resolution** so `integrations.github.token_ref`, `MOONMIND_GITHUB_TOKEN_REF`, `GITHUB_TOKEN_SECRET_REF`, `WORKFLOW_GITHUB_TOKEN_SECRET_REF`, `GITHUB_TOKEN`, `GH_TOKEN`, and `WORKFLOW_GITHUB_TOKEN` do not drift.

2. **Inject the resolved token into `git push` and `gh`**, or replace `gh pr create` with the existing REST PR service.

3. **Document and validate fine-grained permission profiles**, especially `Contents: write`, `Pull requests: write`, `Commit statuses: read`, `Checks: read`, and `Issues: read`.

4. **Return missing-permission diagnostics** instead of generic HTTP 403/404 failures.

5. **Use GitHub Apps** for multi-org, outside-collaborator, or long-lived organization automation cases where fine-grained PATs still cannot cover the workflow.

[1]: https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/api_service/services/settings_catalog.py "raw.githubusercontent.com"
[2]: https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/moonmind/publish/service.py "raw.githubusercontent.com"
[3]: https://cli.github.com/manual/gh_help_environment?utm_source=chatgpt.com "gh environment"
[4]: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens "Managing your personal access tokens - GitHub Docs"
[5]: https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/moonmind/indexers/github_indexer.py "raw.githubusercontent.com"
[6]: https://docs.github.com/rest/pulls/pulls "REST API endpoints for pull requests - GitHub Docs"
[7]: https://raw.githubusercontent.com/MoonLadderStudios/MoonMind/main/moonmind/workflows/adapters/github_service.py "raw.githubusercontent.com"
[8]: https://github.blog/changelog/2023-08-10-x-accepted-github-permissions-header-for-fine-grained-permission-actors/?utm_source=chatgpt.com "X-Accepted-GitHub-Permissions header for fine-grained ..."
````

## Classification

- Input type: Single-story runtime feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the request can be validated through one independently testable operator outcome: GitHub automation uses the configured credential explicitly and reports repository-specific fine-grained permission diagnostics.
- Selected mode: Runtime.
- Source design: The linked public GitHub and GitHub Docs references are preserved as source references from the user request; this spec treats the request text as canonical and maps the recommended changes below.
- Source design path input: `.`.
- Resume decision: No existing active `spec.md` for these token improvements was provided, so specification is the first incomplete stage.

## User Story - Use Fine-Grained GitHub Credentials Reliably

**Summary**: As a MoonMind operator, I want GitHub indexing, publishing, pull request readiness, and credential validation to use the GitHub token I configured for the target repository so that fine-grained personal access tokens work predictably without hidden dependency on ambient `git` or `gh` credentials.

**Goal**: MoonMind consistently resolves one GitHub credential source, passes that credential only to repository operations that need it, validates target-repository permission profiles, degrades optional readiness evidence clearly when permissions are missing, and returns redaction-safe diagnostics that identify the required GitHub permission instead of generic authorization failures.

**Independent Test**: Configure a fine-grained GitHub credential for a selected repository, run repository indexing, publish a branch and create a pull request, evaluate PR readiness with one optional permission missing, and run a targeted token probe. The feature passes when every operation uses the configured credential without pre-existing machine-level `git` or `gh` auth, secret values remain redacted, and missing permissions are reported with specific actionable diagnostics.

**Acceptance Scenarios**:

1. **Given** a GitHub token is provided explicitly, through supported direct token variables, or through a supported secret reference, **When** any GitHub-backed indexing, publishing, readiness, or runtime materialization flow needs a credential, **Then** MoonMind resolves the credential through one documented precedence model and reports which non-secret source category won.
2. **Given** a fine-grained GitHub credential is configured in MoonMind settings, **When** publishing pushes a branch and creates a pull request, **Then** publishing succeeds without relying on pre-existing host `git` credentials or `gh auth` state and without exposing the token in logs, artifacts, or command output.
3. **Given** a publish operation cannot create or update a pull request because the token lacks a required repository permission, **When** MoonMind reports the failure, **Then** the diagnostic includes HTTP status, GitHub's sanitized message, documentation URL when available, and the accepted GitHub permissions header when available.
4. **Given** PR readiness evaluation reaches an optional evidence source such as checks or issue reactions and the token lacks that optional read permission, **When** readiness is evaluated, **Then** the readiness result marks that evidence unavailable with the specific missing permission and continues evaluating other available evidence.
5. **Given** an operator validates a GitHub credential for a selected repository and mode, **When** MoonMind runs the token probe, **Then** it checks only that target repository and reports the repository access and mode-specific permission checklist rather than relying on classic token scopes or global repository listing.
6. **Given** an operator views GitHub token guidance, **When** they compare indexing, publishing, readiness, workflow-file modification, and long-lived automation use cases, **Then** MoonMind identifies the minimum fine-grained permissions and states when a fine-grained personal access token is not an appropriate solution.

### Edge Cases

- Multiple token sources are present at the same time, and MoonMind must apply the documented precedence without leaking token values.
- A Settings catalog token reference is configured through `integrations.github.token_ref` or `MOONMIND_GITHUB_TOKEN_REF`, and runtime GitHub code must resolve the same reference.
- A publish target uses an SSH remote even though the configured credential is a personal access token intended for HTTPS Git operations.
- A token has enough permission to create a pull request but not enough permission to read checks or issue reactions during readiness evaluation.
- GitHub returns a 403 or 404 response with useful permission metadata, and MoonMind must preserve actionable fields while redacting secrets.
- A token targets the wrong resource owner, excludes the selected repository, is pending organization approval, or falls into a known fine-grained PAT limitation such as multi-organization automation.

## Assumptions

- Runtime mode is required; documentation alone does not satisfy the request.
- The selected story covers GitHub credential resolution, GitHub-backed publish/index/readiness behavior, diagnostics, validation, and operator guidance because these are all required for the single outcome of reliable fine-grained GitHub token use.
- Existing secret storage and secret reference mechanisms remain the source of secret values; this story does not require new persistent storage.
- GitHub App guidance is informational and eligibility-focused for this story; building a GitHub App integration is out of scope.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | User request §1 and Bottom line item 1 | GitHub credential resolution must use one canonical precedence model covering explicit tokens, direct token environment variables, secret-reference variables, and the Settings catalog token reference. | In scope | FR-001, FR-002, FR-003 |
| DESIGN-REQ-002 | User request §2 and Bottom line item 2 | GitHub publish operations must stop depending on ambient `git` or `gh` credentials and must provide the resolved credential to branch push and pull request creation paths with redaction. | In scope | FR-004, FR-005, FR-006, FR-007 |
| DESIGN-REQ-003 | User request §3 and Bottom line item 3 | MoonMind must define and validate repository-specific fine-grained token permission profiles for indexing, publishing, PR readiness, workflow-file changes, and full PR automation. | In scope | FR-008, FR-009, FR-010 |
| DESIGN-REQ-004 | User request §4 | Optional readiness checks that lack permissions must degrade gracefully with a specific unavailable-evidence reason instead of making the whole token appear invalid. | In scope | FR-011, FR-012 |
| DESIGN-REQ-005 | User request §5 and Bottom line item 4 | GitHub authorization failures must surface sanitized provider diagnostics, including status, message, documentation URL, and accepted permissions header when available. | In scope | FR-013, FR-014 |
| DESIGN-REQ-006 | User request §6 | Token validation must probe the exact target repository and mode-specific operations rather than relying on classic PAT scopes or global repository lists. | In scope | FR-015, FR-016 |
| DESIGN-REQ-007 | User request §7 and Bottom line item 5 | Operator guidance must identify fine-grained PAT limitations and when GitHub Apps are the appropriate long-lived or multi-organization automation path. | In scope | FR-017 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide one canonical GitHub credential resolution behavior for GitHub API, indexing, publishing, and runtime materialization flows.
- **FR-002**: GitHub credential resolution MUST apply this precedence: explicit token, direct token variables (`GITHUB_TOKEN`, `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`), secret-reference variables (`GITHUB_TOKEN_SECRET_REF`, `WORKFLOW_GITHUB_TOKEN_SECRET_REF`), then Settings catalog token reference (`MOONMIND_GITHUB_TOKEN_REF` / `integrations.github.token_ref`).
- **FR-003**: The system MUST expose redaction-safe diagnostics that identify the selected credential source category without exposing token values or raw secret references beyond allowed reference identifiers.
- **FR-004**: Publishing MUST push branches using the resolved credential and MUST disable interactive credential prompting.
- **FR-005**: Publishing MUST create pull requests using the resolved credential through a deterministic GitHub operation path that does not require ambient `gh auth` state.
- **FR-006**: Any retained `gh` subprocess path MUST receive the resolved credential through supported non-interactive environment variables and repository targeting metadata.
- **FR-007**: Publishing MUST redact the resolved token and secret-like values from command logs, errors, artifacts, and user-facing diagnostics.
- **FR-008**: The system MUST define a repository indexing permission profile requiring repository access to the selected repo and contents read capability.
- **FR-009**: The system MUST define a branch publishing and pull request permission profile requiring contents write and pull requests write, with workflow-file changes requiring workflow write.
- **FR-010**: The system MUST define a PR readiness permission profile covering pull request read, commit status read, checks read, and issue read when reaction fallback is enabled.
- **FR-011**: PR readiness evaluation MUST distinguish required evidence failures from optional evidence that is unavailable because of missing optional permissions.
- **FR-012**: Missing optional checks or issue-reaction permissions MUST produce an actionable readiness note naming the missing permission and the affected evidence source.
- **FR-013**: GitHub API failure diagnostics MUST include HTTP status and GitHub's sanitized response message when provided.
- **FR-014**: GitHub API failure diagnostics MUST include GitHub's documentation URL and accepted permissions header when those fields are present.
- **FR-015**: Token validation MUST probe the selected target repository and selected validation mode rather than relying on classic OAuth scopes or global repository listing.
- **FR-016**: Publish-mode token validation MUST present a preflight checklist for contents write, pull requests write, optional workflow write, commit status read, checks read, and issue read when applicable.
- **FR-017**: Operator guidance MUST document cases where fine-grained personal access tokens cannot satisfy the workflow, including wrong resource owner, excluded repository, pending organization approval, multi-organization automation, outside collaborator limits, and long-lived organization automation better served by GitHub Apps.
- **FR-018**: Automated coverage MUST prove the credential resolver precedence, publish credential injection, redaction, readiness degradation, permission diagnostics, and targeted token probe behavior at the relevant service or workflow boundary.

### Key Entities

- **GitHub Credential Source**: A redaction-safe category describing how a GitHub token was selected, such as explicit input, direct environment token, secret reference, or Settings catalog token reference.
- **GitHub Permission Profile**: A named set of repository access and repository permissions required for one MoonMind GitHub operation mode.
- **GitHub Permission Diagnostic**: A sanitized authorization result containing provider status, message, documentation reference, accepted permission metadata, and an actionable remediation note.
- **Token Probe Result**: The repository-specific validation output that reports target repository reachability, mode-specific permission checks, missing permissions, and known token limitation guidance.
- **Readiness Evidence Availability**: The per-evidence status used during PR readiness evaluation to distinguish available, unavailable due to optional permission, failed required evidence, and not configured.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Credential resolver tests cover at least four source categories and prove the documented precedence including `MOONMIND_GITHUB_TOKEN_REF` and `WORKFLOW_GITHUB_TOKEN`.
- **SC-002**: Publish tests prove branch push and pull request creation use the resolved credential and pass without ambient `git` or `gh` authentication.
- **SC-003**: Redaction tests prove raw token values and secret-like token patterns are absent from publish, validation, and GitHub API failure output.
- **SC-004**: Readiness tests prove missing checks read or issues read permission produces a specific unavailable-evidence note while other readiness evidence is still evaluated.
- **SC-005**: Diagnostic tests prove GitHub authorization failures preserve HTTP status, sanitized message, documentation URL, and accepted permissions metadata when supplied by GitHub.
- **SC-006**: Token probe tests prove validation targets the selected repository and reports mode-specific permission checklist results without using classic scope assumptions.
- **SC-007**: Operator guidance review confirms indexing, publishing, PR readiness, workflow-file modification, fine-grained PAT limitations, and GitHub App recommendation cases are documented.
