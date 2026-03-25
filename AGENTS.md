# Agent Instructions

## Read Documentation

Read relevant documents in the following order before implementing tasks:

1. **Constitution:** `.specify/memory/constitution.md` for non-negotiable principles and constraints
2. **Standards:** Code style and guidance in `README.md`
3. **Spec:** `specs/<feature-id>/spec.md`, then `plan.md`, then `tasks.md`
4. **Docs:** `docs/*.md` as needed for system architecture (see **Documentation: canonical vs tmp** below).
5. **Migration / implementation backlog (when relevant):** `docs/tmp/remaining-work/` and `docs/tmp/PlansOverview.md` for plan-shaped or in-flight work tied to canonical docs.

## Documentation: canonical vs `docs/tmp`

- **Canonical docs** (`docs/` except `docs/tmp/`): describe **declarative desired state** — architecture, contracts, operator-visible behavior, target semantics. Avoid making phased migration or implementation checklists the main story in these files.
- **Migration and implementation notes** belong under **`docs/tmp/`** (e.g. per-doc trackers in `docs/tmp/remaining-work/`, indexes in `docs/tmp/PlansOverview.md`) so they can be **removed when the work completes** without rewriting the canonical spec.
- Align with **Constitution principle XII** in `.specify/memory/constitution.md`.

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
- **Workflow Boundary Coverage**: Any change to Temporal workflows, activity signatures, signal/update names, serialized payload shapes, status normalization, or adapter-to-workflow contracts MUST add or update tests at the workflow boundary, not just isolated unit tests. At minimum:
  - cover the real invocation shape used by the worker binding or Temporal activity wrapper,
  - cover one compatibility case for the previous payload/history shape when runs may already be in flight,
  - cover degraded provider input such as unknown, blank, or newly introduced status values.
- **Replay / In-Flight Safety**: If a change can affect already-running workflows or persisted payloads, add a compatibility or replay-style regression test, or document why in-flight compatibility is impossible and how the cutover is made safe.

## Agent Job Storage Locations
- Agent jobs are executed in a per-run workspace directory named with the job UUID.
- In a Docker worker container, look under `/work/agent_jobs/<job_id>/`.
- Per-job artifacts for those runs are under `/work/agent_jobs/<job_id>/artifacts`, and the checked-out repo is at `/work/agent_jobs/<job_id>/repo`.
- To inspect a run from the host, use the Docker volume directly:
  `docker run --rm -v agent_workspaces:/work/agent_jobs -it -v /tmp:/host_tmp alpine sh -lc 'ls /work/agent_jobs/<job_id> | head'`.
- In the repository code/docs path, durable workflow artifacts for workflow automation are typically written to `var/artifacts/<scope>/<run_id>` (for example `var/artifacts/spec_workflows/<run_id>`).

## Troubleshooting Temporal Workflow Runs

When asked to check on a workflow, follow this procedure in order:

1. **Describe the parent workflow** (always use `--namespace moonmind`):
   ```
   docker exec moonmind-temporal-1 temporal workflow describe \
     --namespace moonmind --workflow-id "<workflow-id>"
   ```
   Check: Status, StartTime, StateTransitionCount, HistoryLength, Pending Activities, Pending Child Workflows.

2. **If the parent has pending child workflows**, describe each child:
   ```
   docker exec moonmind-temporal-1 temporal workflow describe \
     --namespace moonmind --workflow-id "<child-workflow-id>"
   ```

3. **Inspect recent history** of whichever workflow is actively executing (the deepest pending child):
   ```
   docker exec moonmind-temporal-1 temporal workflow show \
     --namespace moonmind --workflow-id "<workflow-id>" | tail -30
   ```
   A healthy agent poll loop looks like: `ActivityTaskScheduled → Started → Completed → TimerStarted → TimerFired` repeating on ~10s intervals. If the last event is an `ActivityTaskScheduled` with no `Started` for minutes, the worker may be down or the task queue starved.

4. **List workflows** when the ID is unknown or to find related runs:
   ```
   docker exec moonmind-temporal-1 temporal workflow list \
     --namespace moonmind --query "mm_state = 'executing'"
   ```

Key diagnostics:
- **Pending Activities > 0 with no progress**: worker may be down — check `docker ps` and worker logs.
- **TimerStarted as last event**: workflow is sleeping between poll cycles — normal, wait for it to fire.
- **ActivityTaskFailed / WorkflowTaskFailed**: read the failure details in the event history JSON output (`--output json`).
- **"workflow not found"**: always retry with `--namespace moonmind` — the default namespace is empty.

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
- MoonMind is a **pre-release project** (see Constitution Principle XIII). Do NOT introduce compatibility aliases, translation layers, or backward-compat wrappers for internal contracts. When a pattern is superseded, **remove the old version entirely** in the same change.
- When refactoring an activity name, model, or interface: grep the entire codebase, update every caller, test, mock, and doc reference, and delete the old artifact. Partial migrations are not acceptable.
- Never introduce compatibility transforms that change execution semantics or billing-relevant values (for example model identifiers, effort values, queue semantics, or publish behavior).
- Prefer fail-fast behavior for unsupported runtime input values over hidden fallback behavior.
- For Codex execution specifically, `codex.model` and `codex.effort` inputs must be passed through exactly as provided. Unsupported values must fail through normal CLI/API validation.
- For Temporal-facing contracts specifically, treat workflow/activity/update/signal payload shapes as compatibility-sensitive. Signature or schema changes MUST preserve worker-bound invocation compatibility for in-flight runs, or be versioned with an explicit migration/cutover plan.

## Shared Skills Runtime
- MoonMind now materializes one per-run active skill set and exposes it to both CLIs through adapter links.
- Expected adapter layout per run: `.agents/skills -> ../skills_active` and `.gemini/skills -> ../skills_active`.
- Default local-only mirror root is `.agents/skills/local`; shared mirror root defaults to `.agents/skills` (`.agents/skills/skills` is legacy nested compatibility).
- Prefer configuring `WORKFLOW_SKILLS_WORKSPACE_ROOT` and `WORKFLOW_SKILLS_CACHE_ROOT` for writable runtime paths in local and CI environments.
