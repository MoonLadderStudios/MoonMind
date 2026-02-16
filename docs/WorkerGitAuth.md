# Worker Git Authentication (Fast Path for Task Jobs)

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-16

## 1. Purpose

Define the fastest safe approach for MoonMind workers to clone/push GitHub repositories for canonical Task queue jobs (`type="task"`).

## 2. Decision

Use worker-level environment authentication with:

- `GITHUB_TOKEN`

On worker startup, configure GitHub auth for `git` using `gh auth`, then execute standard Task stage flow (`prepare -> execute -> publish`).

## 3. Why this is the fastest path

1. No secret material in queue payloads.
2. No immediate requirement for Vault resolver in baseline path.
3. Existing Git operations continue to work with minimal runtime changes.
4. Compatible with later migration to secret references (`repoAuthRef`) in `docs/SecretStore.md`.

## 4. Current Task Behavior Summary

- Workers clone repositories during `moonmind.task.prepare`.
- Publish stage commits and pushes when `task.publish.mode` is `branch` or `pr`.
- PR mode uses GitHub CLI and requires branch push access.
- Repository values must be slug/URL only, never tokenized URLs.

## 5. Fast Path Implementation

### 5.1 Configure worker environment

Set `GITHUB_TOKEN` on each worker runtime (Compose, VM, or secret injection layer).

Recommended token permissions:

- `Contents: Read and write` (clone + push)
- `Pull requests: Read and write` (when using `publish.mode = pr`)

Scope to required repositories only.

### 5.2 Worker startup preflight

Before claim loop:

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

4. Fail fast if setup/check fails.

### 5.3 Keep repository values token-free

Allowed payload values:

- `owner/repo`
- `https://github.com/owner/repo.git`
- `git@github.com:owner/repo.git` (when SSH is intentionally supported)

Never allow:

- `https://<token>@github.com/owner/repo.git`

## 6. Logging and Safety Requirements

- Never log `GITHUB_TOKEN`.
- Do not emit full environment dumps.
- Redact token-like strings in command output and exception traces.
- Keep secret material out of queue events and artifacts.

## 7. Operational Runbook

When clone fails:

1. Confirm startup preflight succeeded.
2. Run inside worker environment:

```bash
gh auth status --hostname github.com
git ls-remote https://github.com/<owner>/<repo>.git
```

3. If unauthorized, rotate token and restart workers.

When push/PR fails:

1. Validate token write/PR permissions.
2. Validate worker token policy allows repository and job type.
3. Confirm publish mode and branch selection from `task_context.json`.

## 8. Guardrails

- Enforce queue worker token repository allowlists.
- Prefer separate tokens per environment/worker group.
- Rotate on a fixed cadence (for example every 30 days).

## 9. Exit Criteria for Fast Path

1. Private repo clone works for canonical Task jobs.
2. Publish branch/PR works without token-in-URL patterns.
3. Logs/artifacts show no token exposure.
4. Token rotation works with no code changes.

## 10. Next Step

Move to Vault-backed secret references (`repoAuthRef`) for hardened private repo auth (`docs/SecretStore.md`).
