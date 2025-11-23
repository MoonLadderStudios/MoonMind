# Jules Agent Environment Guide

This document outlines the steps required to compile the project and run unit tests from within the Jules Agent environment. Following these instructions will ensure that the code is valid and passes basic tests before pushing to GitHub Actions.

## Compiling the Project

The project is built using Python, and the dependencies are managed by Poetry. To compile the project, you need to install the required dependencies.

### 1. Install Dependencies

Use the following command to install all project dependencies, including those required for running tests:

```bash
poetry install -E tests
```

This command reads the `pyproject.toml` file and installs all the necessary packages. The `-E tests` flag ensures that the testing libraries (like `pytest`) are also installed.

## Running Unit Tests

Once the dependencies are installed, you can run the unit tests to verify the correctness of the code.

### 1. Execute the Test Suite

To run the unit tests, use the following command:

```bash
./tools/test_unit.sh
```

This command will discover and run all the tests in the `tests/unit` directory. If all tests pass, you can be confident that your changes have not introduced any regressions and that the project is in a stable state.

### Test layers & CI

- Unit tests
  - Location: `tests/unit/...`
  - CI Workflow: `.github/workflows/pytest-unit-tests.yml`
  - Command: `./tools/test_unit.sh`
  - Used by: GitHub Actions + Codex Cloud Agent

- Orchestrator integration tests
  - Entry point: `docker-compose.test.yaml` (`orchestrator-tests` service)
  - CI Workflow: `.github/workflows/orchestrator-integration-tests.yml`
  - Runs on: push to `main` + manual `workflow_dispatch`
