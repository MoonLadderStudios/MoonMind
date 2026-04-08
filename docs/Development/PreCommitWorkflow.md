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

The current GitHub unit-test workflow runs one dedicated frontend validation job, a separate generated-contract check that only runs when `tools/check_openapi_affecting_changes.sh` reports an OpenAPI-affecting path, plus `./tools/test_unit.sh` for backend unit tests.

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
4. Run `./tools/test_unit.sh` for the canonical unit-test pass
5. Run targeted integration or end-to-end scripts only when your change needs them

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
- `package.json`
- `tools/test_unit.sh`
- `tools/test_unit_docker.sh`
- `tools/generate_openapi_types.py`
- `tools/test-unit.ps1`
- `tools/test-integration.ps1`
- `tools/test-e2e.ps1`
