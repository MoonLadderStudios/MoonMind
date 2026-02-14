# Worker Git Authentication (Fastest Path)

## Purpose

This document defines the fastest safe approach to let MoonMind workers clone and push private GitHub repositories now, without redesigning the queue model.

## Decision

Use one environment variable on the worker host/container:

- `GITHUB_TOKEN`

On worker startup, configure GitHub auth for `git` using `gh auth`, then keep the current clone/push flow unchanged.

## Why this is the fastest path

1. No queue schema changes are required.
2. No `repoAuthRef` resolver is required yet.
3. Existing worker behavior (`git clone`, `git push`, optional `gh pr create`) continues to work with minimal code changes.
4. Git credentials stay out of job payloads and repository URLs.

## Current behavior summary

- The worker already clones repos automatically for `codex_exec` jobs when needed.
- The worker accepts `repository` as slug/URL and converts slug to `https://github.com/<slug>.git`.
- The worker can push branches and create PRs if publish mode is enabled.
- There is no PAT-aware clone setup in the worker runtime today.

## Fast path implementation

## 1) Configure worker environment

Set `GITHUB_TOKEN` on each worker runtime (Compose service, VM, or secret injection layer).

Token guidance:

- Prefer GitHub fine-grained PATs.
- Scope to only required repositories.
- Required permissions should usually include:
- `Contents: Read and write` (for clone + push)
- `Pull requests: Read and write` (if using `publish.mode = pr`)

## 2) Run startup preflight auth in worker CLI

Before entering the poll loop:

1. Verify `gh` exists.
2. If `GITHUB_TOKEN` is present, run:

```bash
printf '%s' "$GITHUB_TOKEN" | gh auth login --hostname github.com --with-token
gh auth setup-git
```

3. Validate with:

```bash
gh auth status --hostname github.com
```

4. If auth setup fails, exit fast with a clear error.

This keeps `git clone https://github.com/org/repo.git` working for private repos without embedding token material in command args.

## 3) Keep repository values token-free

Producers must only send:

- `owner/repo`
- `https://github.com/owner/repo.git`
- `git@github.com:owner/repo.git` (if SSH is explicitly supported in that environment)

Never send:

- `https://<token>@github.com/owner/repo.git`

## 4) Logging and safety requirements

- Never log `GITHUB_TOKEN`.
- Do not print full environment dumps in worker logs.
- Keep command logs enabled, but ensure no command includes raw token values.
- Keep PAT out of queue payloads and out of artifact contents.

## 5) Operational runbook

When clone fails:

1. Check worker startup logs for GitHub auth preflight result.
2. Run in worker container:

```bash
gh auth status --hostname github.com
git ls-remote https://github.com/<owner>/<repo>.git
```

3. If unauthorized:
- Rotate or replace `GITHUB_TOKEN`.
- Restart worker.

When push/PR fails:

1. Confirm token has repository write + PR permissions.
2. Confirm repo allowlist and worker token policy permit the target repo/job.

## 6) Guardrails for now

- Continue using queue worker token policy (`allowedRepositories`) to reduce blast radius.
- Use one PAT per worker group/environment, not one PAT shared across unrelated environments.
- Rotate PATs on a fixed cadence (for example every 30 days).

## 7) Exit criteria for this phase

This fast path is complete when:

1. Private repo clone works with slug and URL payloads.
2. Publish branch/PR works without token-in-URL patterns.
3. Worker logs contain no token leaks.
4. Token rotation can be performed without code changes.

## Next step

Move from env PAT to Vault-backed `repoAuthRef` resolution (see `SecretStore.md`) once private repo workflows are stable.
