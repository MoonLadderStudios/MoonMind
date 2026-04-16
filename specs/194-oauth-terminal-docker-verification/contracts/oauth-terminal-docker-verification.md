# Contract: OAuth Terminal Docker Verification

## Verification Command Contract

Command:

```bash
./tools/test_integration.sh
```

Expected behavior:
- Runs the repo's hermetic integration suite marked `integration_ci`.
- Requires Docker access and the compose-backed test environment.
- Produces enough evidence to evaluate OAuthTerminal-relevant managed-session coverage.

Blocked behavior:
- If `/var/run/docker.sock` is unavailable or the Docker daemon cannot be reached, the story remains ADDITIONAL_WORK_NEEDED and reports record the exact blocker.

## Evidence Contract

Evidence used for closure must include:
- Managed Codex session launch mounting `agent_workspaces` at `/work/agent_jobs`.
- Conditional auth volume mount only at `MANAGED_AUTH_VOLUME_PATH`.
- Pre-container rejection when `MANAGED_AUTH_VOLUME_PATH` equals `codexHomePath`.
- Runtime startup one-way seeding from valid auth-volume path into per-run `CODEX_HOME`.
- Docker-backed OAuth terminal auth runner and PTY bridge lifecycle behavior.

Evidence must not include:
- Raw credential files or token values.
- Full environment dumps.
- Full Docker Compose logs pasted into reports.
- Auth headers, private keys, cookies, or session IDs.

## Report Update Contract

Affected reports:
- `specs/175-launch-codex-auth-materialization/verification.md`
- `specs/180-codex-volume-targeting/verification.md`
- `specs/183-oauth-terminal-flow/verification.md`

Update rules:
- Change a verdict from ADDITIONAL_WORK_NEEDED only when the specific Docker-backed gap in that report is covered by passing evidence.
- If Docker is unavailable, preserve the blocker and reference `MM-363`.
- If integration fails for a product or harness defect, record the failing command and a concise redacted failure summary.
