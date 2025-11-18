# Pre-commit Integration

## Overview

All test scripts in MoonMind now automatically run pre-commit checks before executing tests. This ensures code quality and formatting consistency before tests run.

## What Changed

### Test Scripts Updated

The following PowerShell test scripts now include pre-commit checks:

1. **`tools/test-unit.ps1`** - Unit tests
2. **`tools/test-integration.ps1`** - Integration tests
3. **`tools/test-e2e.ps1`** - End-to-end tests

### How It Works

When you run any test script, it will:

1. **Run pre-commit checks** (black, isort, ruff)
   - If checks **pass**: proceed to run tests
   - If checks **fail**: script exits with error message

2. **Build Docker containers** (if checks passed)

3. **Execute tests** (if checks passed)

## Example Output

### Success Case
```powershell
PS> .\tools\test-unit.ps1
Running pre-commit checks...
black....................................................................Passed
isort....................................................................Passed
ruff.....................................................................Passed
Pre-commit checks passed!

[Docker build and test execution continues...]
```

### Failure Case
```powershell
PS> .\tools\test-unit.ps1
Running pre-commit checks...
black....................................................................Failed
- hook id: black
- files were modified by this hook

reformatted tests/unit/workflows/orchestrator/test_tasks.py

Pre-commit checks failed. Please fix formatting issues and commit changes.
```

## Installation

Pre-commit must be installed on your system to use these scripts:

```bash
# Install pre-commit
pip install pre-commit

# Install git hooks (optional, for commit-time checks)
pre-commit install
```

## Manual Pre-commit Checks

You can manually run pre-commit checks without running tests:

```bash
# Check all files
pre-commit run --all-files

# Check specific files
pre-commit run --files tests/unit/workflows/orchestrator/test_tasks.py

# Auto-fix issues (where possible)
pre-commit run --all-files
git add .  # Add the formatted files
```

## CI/CD Integration

The CI/CD pipeline also runs pre-commit checks. If formatting issues are detected:

1. CI will fail the build
2. The error message will show which files need formatting
3. Run pre-commit locally to fix issues
4. Commit and push the formatted files

## Troubleshooting

### "pre-commit: command not found"

**Solution:** Install pre-commit:
```bash
pip install pre-commit
```

### Files Keep Getting Reformatted

This is expected behavior! Black, isort, and ruff enforce consistent formatting. After pre-commit formats your files:

1. Review the changes
2. Commit the formatted files
3. Push to trigger CI again

### Want to Skip Pre-commit Temporarily?

**Not recommended**, but if absolutely necessary, you can comment out the pre-commit section in the test scripts. However, CI will still fail if formatting issues exist.

## Benefits

✅ **Catch formatting issues early** - Before running time-consuming tests
✅ **Consistent code style** - Across the entire codebase
✅ **Faster CI builds** - Formatting issues caught locally
✅ **Better code reviews** - Focus on logic, not formatting

## Related Documentation

- [Pre-commit official docs](https://pre-commit.com/)
- [Black code formatter](https://black.readthedocs.io/)
- [isort import sorter](https://pycqa.github.io/isort/)
- [Ruff linter](https://docs.astral.sh/ruff/)

