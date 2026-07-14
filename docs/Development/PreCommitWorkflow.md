# Pre-Commit Workflow

## Overview

MoonMind uses `pre-commit` as a fast local quality gate for Python formatting and lint cleanup before longer test runs.

The current hook set is defined in `.pre-commit-config.yaml` and runs:

- `black`
- `isort --profile=black`
- `ruff --fix`

Frontend build output and generated API types are validated separately. `pre-commit` does not run the asset pipeline because the repository wrappers invoke `pre-commit run --all-files`, and forcing full Vite/OpenAPI regeneration on every wrapper-driven test pass would add unnecessary latency. Use `npm run frontend:ci` for the canonical frontend validation pass, use `npm run generate` to refresh checked-in generated frontend API types, and use `npm run contracts:check` to verify those tracked generated files are in sync. The canonical path detector used by CI lives at `tools/check_openapi_affecting_changes.sh`.

## Current Project Behavior

### PowerShell Test Wrappers

The Windows-oriented PowerShell wrappers still run `pre-commit` before they start their test flow:

- `tools/test-unit.ps1`
- `tools/test-integration.ps1`
- `tools/test-e2e.ps1`

Each script starts with:

```powershell
pre-commit run --all-files
```

If that command fails, the script exits before building containers or running tests.

### Canonical Unit Test Path

The canonical unit test entrypoint for the repository is:

```bash
./tools/test_unit.sh
```

That script is the source of truth for unit-test execution. It does **not** invoke `pre-commit` itself, so run `pre-commit` separately when you want the same formatting and lint guardrail outside the PowerShell wrappers.

In WSL, `./tools/test_unit.sh` automatically delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.

### CI Behavior

The GitHub unit-test workflow uses impact-aware backend suite selection for pull requests. A `select-test-suites` job runs `tools/select_test_suites.py` against the changed files and emits suite decisions for:

- `unit_fast`
- `api_component`
- `temporal_boundary`
- `integration_ci`
- `reliability_journey`
- `full_backend`

Branch protection should require the always-running `ci-required` summary job instead of the conditional backend suite jobs, plus the standalone `migration-gate` check. `ci-required` fails when any suite selected by the selector is skipped, cancelled, or unsuccessful; `migration-gate` independently blocks migration-graph and clean-database upgrade failures.

Routine backend pull requests run the cheap unit safety net first. API/router/auth/db/service changes also run the component suite. Temporal workflow, runtime, activity-boundary, signal/update, replay, or Temporal schema changes run the Temporal boundary suite. Changes under workflow adapters, Temporal workflows, checked-in `.agents/skills` bundles, Docker runtime paths, checkpoint schemas, and reliability replay fixtures run the hermetic reliability journey suite. Docker, integration, database, compose, migration, dependency, or test-runner changes run hermetic `integration_ci` as needed.

The selector fails open. Empty changed-file input, unknown paths, CI workflow changes, dependency file changes, test-runner changes, pytest configuration changes, selector changes, pushes to `main`, scheduled runs, and manual dispatches all force `full_backend=true`. On that path, the workflow runs the canonical backend unit command:

```bash
./tools/test_unit.sh --python-only
```

`./tools/test_unit.sh` reports the slowest Python tests with `--durations`; set `MOONMIND_PYTEST_DURATIONS` to tune the count. In CI it also writes JUnit XML unless `MOONMIND_PYTEST_JUNITXML` points at a different output path.

The workflow still runs a dedicated frontend validation job, and the generated-contract check still runs only when `tools/check_openapi_affecting_changes.sh` reports an OpenAPI-affecting path.

See [Backend Test Selection Strategy](BackendTestSelection.md) for the detailed selector contract, category definitions, full-backend fail-open rules, and maintenance guidance.

CI does **not** currently run a dedicated `pre-commit` step, so local `pre-commit` runs are still the main way to catch formatting and auto-fixable lint issues before pushing.

## Setup

Install `pre-commit` into the Python environment you use for MoonMind development:

```bash
pip install pre-commit
```

Optionally install the Git hook so checks run automatically on `git commit`:

```bash
pre-commit install
```

## Recommended Local Workflow

1. Run `pre-commit run --all-files`
2. Review and stage any files rewritten by the hooks
3. If your change touches the frontend, run `npm run frontend:ci`; if `git diff --name-only ... | bash tools/check_openapi_affecting_changes.sh` succeeds, run `npm run contracts:check`
4. For every remaining MM-822+ Step Execution PR, record the Step Execution conformance gate before claiming merge readiness. The PR checklist must say either:
   - the conformance suite was run with:

     ```bash
     python -m moonmind.workflows.temporal.step_execution_conformance
     pytest tests/unit/workflows/temporal/test_step_executions.py tests/unit/workflows/temporal/test_step_checkpoints.py tests/integration/workflows/temporal/test_step_execution_manifest_evidence.py -q
     ```

   - or no fixture update was needed, with an explicit checklist note explaining why the PR does not change Step Execution behavior or fixture coverage.
5. Run the targeted unit suite selected by the changed area before preparing a PR. Use the selector-equivalent commands below for backend changes, path-filtered `./tools/test_unit.sh` invocations for narrow Python changes, or `./tools/test_unit.sh --ui-args <path>` / `npm run ui:test -- <path>` for focused frontend changes. Escalate to `./tools/test_unit.sh` only when fail-open policy, broad/risky changes, or unclear coverage requires the full unit suite.
6. Use these selector-equivalent backend suites when iterating on a narrow backend change:

```bash
python -m pytest tests/unit \
  -m "not temporal_boundary and not component and not slow" \
  -q -n auto --dist loadfile --durations=25

python -m pytest tests/unit/api tests/unit/api_service tests/component/api \
  -m "component and not temporal_boundary and not slow" \
  -q -n auto --dist loadfile --durations=25

python -m pytest tests/unit/workflows/temporal \
  -m "temporal_boundary and not slow" \
  -q --durations=25
```

7. Run `./tools/test_integration.sh` only when Docker, compose, migrations, integration tests, runtime infrastructure, or another selector-selected hermetic integration boundary changed.

If you prefer the Windows wrappers, `tools/test-unit.ps1`, `tools/test-integration.ps1`, and `tools/test-e2e.ps1` already include step 1 for you.

## Manual Commands

```bash
# Run all configured hooks against the repo
pre-commit run --all-files

# Run hooks only for selected files
pre-commit run --files path/to/file.py
```

## Troubleshooting

### `pre-commit: command not found`

Install the tool into the active Python environment:

```bash
pip install pre-commit
```

### Hooks Rewrote Files

That usually means `black`, `isort`, or `ruff --fix` corrected formatting or lint issues. Re-review the changes, stage them, and rerun your test command.

### Unit Tests Behave Differently In WSL

That is expected by default. `./tools/test_unit.sh` switches to the Docker-backed path in WSL to reduce host-environment drift.

## Related Files

- `.pre-commit-config.yaml`
- `docs/Development/BackendTestSelection.md`
- `package.json`
- `tools/test_unit.sh`
- `tools/test_unit_docker.sh`
- `tools/select_test_suites.py`
- `tools/generate_openapi_types.py`
- `tools/test-unit.ps1`
- `tools/test-integration.ps1`
- `tools/test-e2e.ps1`
