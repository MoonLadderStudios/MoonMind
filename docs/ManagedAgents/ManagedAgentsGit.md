# Managed Agent Git Authentication (Fast Path for Workflows)

**Implementation tracking:** [`docs/tmp/remaining-work/ManagedAgents-ManagedAgentsGit.md`](../tmp/remaining-work/ManagedAgents-ManagedAgentsGit.md)

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-03-14

## 1. Purpose

Define the fastest safe approach for MoonMind Managed Agents to clone/push GitHub repositories for Canonical Temporal Workflows (`AgentTaskWorkflow`).

## 2. Decision

Use sandbox-level environment authentication with:

- `GITHUB_TOKEN`

On `temporal-worker-sandbox` startup, configure GitHub auth for `git` using `gh auth`, then execute standard Temporal Activities (`PrepareWorkspaceActivity -> RunAgentLoopActivity -> PublishActivity`).

## 3. Why this is the fastest path

1. No secret material in Temporal payload histories or workflow inputs.
2. No immediate requirement for Vault resolver in baseline path.
3. Existing Git operations continue to work with minimal runtime changes.
4. Compatible with later migration to SecretRef-based secret resolution in `docs/Security/SecretsSystem.md`.

## 4. Current Workflow Behavior Summary

- Managed Agents clone repositories during `PrepareWorkspaceActivity`.
- `PublishActivity` commits and pushes when `task.publish.mode` is `branch` or `pr`.
- PR mode uses GitHub CLI and requires branch push access.
- Repository values must be slug/URL only, never tokenized URLs.

## 5. Fast Path Implementation

### 5.1 Configure sandbox environment

Set `GITHUB_TOKEN` on each worker runtime (Compose, VM, or secret injection layer) serving the Temporal Task Queues.

Recommended token permissions:

- `Contents: Read and write` (clone + push)
- `Pull requests: Read and write` (when using `publish.mode = pr`)

Scope to required repositories only.

### 5.2 Worker startup preflight

Before starting the Temporal Worker daemon:

1. Verify `gh` exists.
2. If `GITHUB_TOKEN` is present, run:

```bash
printf '%s' "$GITHUB_TOKEN" | gh auth login --hostname github.com --with-token
gh auth setup-git
```

3. Validate:

```bash
gh auth status --hostname github.com
```

4. Fail fast if setup/check fails so the Temporal Worker does not register to start processing Activities.

### 5.3 Keep repository values token-free

Allowed workflow input `repository` values:

- `owner/repo`
- `https://github.com/owner/repo.git`
- `git@github.com:owner/repo.git` (when SSH is intentionally supported)

Never allow:

- `https://<token>@github.com/owner/repo.git`

## 6. Logging and Safety Requirements

- Never log `GITHUB_TOKEN` to Temporal histories or stdout.
- Do not emit full environment dumps into `ActivityExecutionResult` or similar payloads.
- Redact token-like strings in command output and exception traces sent back to the Temporal system.
- Keep secret material out of Temporal events and artifacts.

## 7. Operational Runbook

When an Activity related to cloning fails:

1. Confirm startup preflight succeeded for the Temporal Worker handling that Task Queue.
2. Run inside worker environment (using `docker exec` or similar tools):

```bash
gh auth status --hostname github.com
git ls-remote https://github.com/<owner>/<repo>.git
```

3. If unauthorized, rotate token and restart the Temporal workers.

When `PublishActivity` fails:

1. Validate token write/PR permissions.
2. Validate worker token policy allows repository routing.
3. Confirm publish mode and branch selection from `task_context.json`.

## 8. Guardrails

- Enforce Temporal Task Queue capability routing (only allow specific repositories matching the token scoped to the worker).
- Prefer separate tokens per environment/worker sandbox.
- Rotate on a fixed cadence (for example every 30 days).

## 9. Exit Criteria for Fast Path

1. Private repo clone works for `AgentTaskWorkflow` executions.
2. Publish branch/PR works without token-in-URL patterns inside the `temporal-worker-sandbox`.
3. Temporal Workflow execution histories and artifacts show no token exposure.
4. Token rotation works with no code changes by restarting the worker sandboxes.

## 10. Next Step

Move to SecretRef-based Git credential handling aligned with `docs/Security/SecretsSystem.md` and `docs/Security/ProviderProfiles.md` while preserving the simple local-first operator path.
