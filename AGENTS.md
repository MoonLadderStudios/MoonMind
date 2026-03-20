# Agent Instructions

## Read Documentation

Read relevant documents in the following order before implementing tasks:

1. **Constitution:** `.specify/memory/constitution.md` for non-negotiable principles and constraints
2. **Standards:** Code style and guidance in `README.md`
3. **Spec:** `specs/<feature-id>/spec.md`, then `plan.md`, then `tasks.md`
4. **Docs:** `Docs/*.md` as needed for system architecture

## Spec Numbering

When creating a new spec folder/feature ID:

- ✅ **DO** scan `specs/` and find the highest numeric prefix across all directories matching `<number>-<name>`.
- ✅ **DO** assign the next number globally (`max + 1`), regardless of short-name/topic.
- ✅ **DO** keep branch/feature/spec numbering aligned to that global next number.
- ❌ **DON'T** reset numbering to `001` for a new short-name if higher numbered specs already exist.

## Testing Instructions
- **Unit Tests**: Always use `./tools/test_unit.sh` to run unit tests. This script is the single source of truth for CI and local development, ensuring consistent execution and proper exit codes. It automatically uses `python` and falls back to `python3` when `python` is unavailable. Do not run `pytest` directly or pipe to `tail` as this may mask failures.
- **WSL Unit Test Mode**: In WSL, `./tools/test_unit.sh` automatically delegates to `./tools/test_unit_docker.sh` (unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set) so tests run in the Docker test environment by default. Use this path when working in WSL.
- **Integration Tests**: Run Python integration tests in the test compose image, for example `docker compose -f docker-compose.test.yaml run --rm pytest bash -lc "pytest tests/integration -q --tb=short"`, or use `tools/test-integration.ps1` (no args) for the same default.

## Agent Job Storage Locations
- Agent jobs are executed in a per-run workspace directory named with the job UUID.
- In a Docker worker container, look under `/work/agent_jobs/<job_id>/`.
- Per-job artifacts for those runs are under `/work/agent_jobs/<job_id>/artifacts`, and the checked-out repo is at `/work/agent_jobs/<job_id>/repo`.
- To inspect a run from the host, use the Docker volume directly:
  `docker run --rm -v agent_workspaces:/work/agent_jobs -it -v /tmp:/host_tmp alpine sh -lc 'ls /work/agent_jobs/<job_id> | head'`.
- In the repository code/docs path, durable workflow artifacts for workflow automation are typically written to `var/artifacts/<scope>/<run_id>` (for example `var/artifacts/spec_workflows/<run_id>`).

## Tool Execution Guardrails
- **Strict Verification of Tool Results**: Never hallucinate success or fabricate data when a tool execution fails. If a tool (e.g., `read_file`, `run_shell_command`) returns an error such as 'File not found', you must correctly identify the failure and take appropriate remediating action instead of silently bypassing it.

## Tool Usage
- **Heredocs in `run_shell_command`**: Explicitly forbid the use of bare heredocs (e.g. `<< 'EOF' > file.md`) in `run_shell_command`. You MUST use `cat << 'EOF' > file.md` or the `write_file` tool to prevent Bash parsing errors and subsequent artifact gaps.

## Security Guardrails
- Never post or commit raw credentials (tokens, API keys, passwords, private keys, cookies, auth headers, session IDs).
- Never paste full `docker compose` output, `.env` files, or environment/config dumps into PR comments. Summarize and redact.
- Before posting any PR/issue/review comment, scan the outgoing text for secret-like patterns (`ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, private key blocks, `token=`/`password=` assignments) and block posting on any match.
- If secrets are observed in comments, logs, or commits: stop, redact/delete the exposed content when possible, and rotate affected credentials immediately.

## Compatibility Policy
- Never introduce compatibility transforms that change execution semantics or billing-relevant values (for example model identifiers, effort values, queue semantics, or publish behavior).
- Prefer fail-fast behavior for unsupported runtime input values over hidden fallback behavior.
- For Codex execution specifically, `codex.model` and `codex.effort` inputs must be passed through exactly as provided. Unsupported values must fail through normal CLI/API validation.

## Shared Skills Runtime
- MoonMind now materializes one per-run active skill set and exposes it to both CLIs through adapter links.
- Expected adapter layout per run: `.agents/skills -> ../skills_active` and `.gemini/skills -> ../skills_active`.
- Default local-only mirror root is `.agents/skills/local`; shared mirror root defaults to `.agents/skills` (`.agents/skills/skills` is legacy nested compatibility).
- Prefer configuring `WORKFLOW_SKILLS_WORKSPACE_ROOT` and `WORKFLOW_SKILLS_CACHE_ROOT` for writable runtime paths in local and CI environments.
