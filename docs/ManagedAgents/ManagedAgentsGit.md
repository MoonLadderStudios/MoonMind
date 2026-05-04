# Managed Agent Git Authentication (Fast Path for Workflows)

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

Status: Proposed 
Owners: MoonMind Engineering 
Last Updated: 2026-03-14

## 1. Purpose

Define the fastest safe approach for MoonMind Managed Agents to clone/push GitHub repositories for Canonical Temporal Workflows (`AgentTaskWorkflow`).

## 2. Decision

Use the MoonMind GitHub credential resolver for every GitHub operation. The
resolver accepts, in order:

- `GITHUB_TOKEN`
- `GH_TOKEN`
- `WORKFLOW_GITHUB_TOKEN`
- `GITHUB_TOKEN_SECRET_REF`
- `WORKFLOW_GITHUB_TOKEN_SECRET_REF`
- `MOONMIND_GITHUB_TOKEN_REF` / `integrations.github.token_ref`

Publishing passes the resolved token to git and GitHub API calls directly. `gh`
authentication may exist on a worker, but it is not the authority for managed
publish behavior.

## 3. Why this is the fastest path

1. No secret material in Temporal payload histories or workflow inputs.
2. No immediate requirement for Vault resolver in baseline path.
3. Existing Git operations continue to work with minimal runtime changes.
4. Compatible with later migration to SecretRef-based secret resolution in `docs/Security/SecretsSystem.md`.

## 4. Current Workflow Behavior Summary

- Managed Agents clone repositories during `PrepareWorkspaceActivity`.
- `PublishActivity` commits and pushes when `task.publish.mode` is `branch` or `pr`.
- PR mode pushes the branch with token-aware git auth and creates the pull
  request through the GitHub REST API when a repository-scoped token is
  available.
- Repository values must be slug/URL only, never tokenized URLs.

## 5. Fast Path Implementation

### 5.1 Configure sandbox environment

Set one of the supported token environment variables or configure
`integrations.github.token_ref` to a managed SecretRef.

Recommended fine-grained PAT profile for indexing only:

- Resource owner: the owner of the target repository.
- Repository access: selected repositories only.
- Repository permissions: `Contents: Read`.

Recommended fine-grained PAT profile for managed branch/PR publishing:

- `Contents: Read and write` (clone + push)
- `Pull requests: Read and write` (create/update PRs)
- `Commit statuses: Read` (readiness checks)
- `Checks: Read` (readiness checks)
- `Issues: Read` (reaction-based review fallback)
- `Workflows: Write` only when agents may modify `.github/workflows/*`

Scope to required repositories only.

### 5.2 Worker startup preflight

Before starting the Temporal Worker daemon:

1. Verify the intended credential source is present.
2. Run the targeted token probe for the exact `owner/repo` and publish mode.
3. If local operator tooling needs `gh`, login with `GH_TOKEN`; do not rely on
   `gh auth` for managed publish correctness.

```bash
curl -X POST /api/v1/settings/github/token-probe \
  -H 'content-type: application/json' \
  -d '{"repo":"owner/repo","mode":"publish","baseBranch":"main"}'
```

Fail fast if the probe cannot access the selected repository or reports missing
required permissions.

### 5.3 Keep repository values token-free

Allowed workflow input `repository` values:

- `owner/repo`
- `https://github.com/owner/repo.git`
- `git@github.com:owner/repo.git` (when SSH is intentionally supported)

Never allow:

- `https://<token>@github.com/owner/repo.git`

## 6. Logging and Safety Requirements

- Never log `GITHUB_TOKEN` to Temporal histories or stdout.
- Never log `GH_TOKEN`, `WORKFLOW_GITHUB_TOKEN`, or resolved SecretRef values.
- Do not emit full environment dumps into `ActivityExecutionResult` or similar payloads.
- Redact token-like strings in command output and exception traces sent back to the Temporal system.
- Keep secret material out of Temporal events and artifacts.

## 7. Operational Runbook

When an Activity related to cloning fails:

1. Confirm the targeted token probe succeeds for the selected repository.
2. Run inside worker environment (using `docker exec` or similar tools):

```bash
git ls-remote https://github.com/<owner>/<repo>.git
```

3. If unauthorized, rotate token and restart the Temporal workers.

When `PublishActivity` fails:

1. Validate token write/PR permissions.
2. Validate worker token policy allows repository routing.
3. Confirm publish mode and branch selection from `task_context.json`.
4. Inspect any GitHub diagnostic fields, especially
   `X-Accepted-GitHub-Permissions`.

## 8. Fine-Grained PAT Limits

Fine-grained PATs must be created for the target resource owner and include the
selected repository. Organization approval can leave a token unable to access
private resources until approval completes.

Fine-grained PATs do not cover every automation shape. Multi-organization
automation, outside-collaborator organization repository access, long-lived
organization automation, and SSH-only Git remotes should use a GitHub App or an
operator-approved classic PAT instead.

## 9. Guardrails

- Enforce Temporal Task Queue capability routing (only allow specific repositories matching the token scoped to the worker).
- Prefer separate tokens per environment/worker sandbox.
- Rotate on a fixed cadence (for example every 30 days).

## 10. Exit Criteria for Fast Path

1. Private repo clone works for `AgentTaskWorkflow` executions.
2. Publish branch/PR works without token-in-URL patterns inside the `temporal-worker-sandbox`.
3. Temporal Workflow execution histories and artifacts show no token exposure.
4. Token rotation works with no code changes by restarting the worker sandboxes.

## 11. Next Step

Move to SecretRef-based Git credential handling aligned with `docs/Security/SecretsSystem.md` and `docs/Security/ProviderProfiles.md` while preserving the simple local-first operator path.
