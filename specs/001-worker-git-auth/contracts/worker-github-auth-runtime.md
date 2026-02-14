# Runtime Contract: Worker GitHub Authentication Fast Path

## Startup Contract

1. Worker startup preflight validates `codex` CLI and Codex login state.
2. When `GITHUB_TOKEN` is set:
   - worker verifies `gh` CLI is executable,
   - worker runs `gh auth login --hostname github.com --with-token` using token via stdin,
   - worker runs `gh auth setup-git`,
   - worker verifies `gh auth status --hostname github.com`.
3. Any failed startup auth step exits worker startup with a clear error before polling.

## Repository Input Contract

Accepted repository payload forms:

- `owner/repo`
- `https://github.com/owner/repo.git`
- `git@github.com:owner/repo.git` (environment-dependent SSH support)

Rejected repository payload forms:

- Any tokenized HTTPS URL containing credentials, including `https://<token>@github.com/owner/repo.git`.

## Logging/Safety Contract

- Command logging remains enabled for operational visibility.
- Raw secret values (notably `GITHUB_TOKEN`) must never appear in:
  - startup/preflight error output,
  - handler command logs,
  - uploaded execution artifacts.

## Compatibility Contract

- Queue payload schema and worker claim API remain unchanged.
- Existing publish modes (`none`, `branch`, `pr`) continue to use current flow.
- Existing worker repository allowlist and policy gates remain enforced by queue auth service.
