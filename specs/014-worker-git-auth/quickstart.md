# Quickstart: Worker GitHub Token Authentication Fast Path

## Prerequisites

- MoonMind worker runtime dependencies available: `codex`, `git`, `gh`.
- Queue API reachable via `MOONMIND_URL`.
- Worker token configured if queue worker auth is enabled.
- GitHub PAT exported as `GITHUB_TOKEN` with repository permissions needed for target clone/push/PR operations.

## 1) Configure environment

```bash
export MOONMIND_URL="http://localhost:5000"
export MOONMIND_WORKER_ID="executor-01"
export MOONMIND_WORKER_TOKEN="<worker-token-if-required>"
export GITHUB_TOKEN="<github-pat>"
```

## 2) Run worker preflight once

```bash
python -m moonmind.agents.codex_worker.cli --once
```

Expected behavior:

- Startup verifies Codex login state.
- Startup configures GitHub auth (`gh auth login` + `gh auth setup-git`) when token is present.
- Startup verifies `gh auth status` before processing claims.

## 3) Validate repository input guardrails

- Allowed payload repository values:
  - `owner/repo`
  - `https://github.com/owner/repo.git`
  - `git@github.com:owner/repo.git`
- Disallowed value:
  - `https://<token>@github.com/owner/repo.git`

## 4) Verify no token leakage in logs/artifacts

- Run one `codex_exec` job and inspect generated `codex_exec.log` artifact.
- Confirm raw `GITHUB_TOKEN` value does not appear in startup errors or command logs.

## 5) Run unit validation

```bash
./tools/test_unit.sh
```

## 6) Token rotation check

- Replace `GITHUB_TOKEN` with a rotated PAT value.
- Restart worker process.
- Re-run one private repository job; confirm clone/publish still succeeds without code changes.
