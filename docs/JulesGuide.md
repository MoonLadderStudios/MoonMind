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
poetry run pytest
```

This command will discover and run all the tests in the `tests/` directory. If all tests pass, you can be confident that your changes have not introduced any regressions and that the project is in a stable state.

By following these steps, you can ensure that your code is always in a compilable and testable state before sharing it with the rest of the team.
