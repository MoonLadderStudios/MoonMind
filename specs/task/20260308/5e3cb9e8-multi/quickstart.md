# Quickstart: Temporal Local Dev Bring-up Path & E2E Test

## Overview

This guide explains how to bring up the local development environment using Temporal and execute the End-to-End test suite.

## Starting the Environment

To start the Temporal server, its backing databases, and all the required MoonMind worker fleets, run:

```bash
docker compose --profile temporal up -d
```
*(Note: If `temporal` is part of the default profile, just `docker compose up -d` might suffice based on configuration)*

Verify that the containers are running and that the Temporal workers are polling:

```bash
docker compose logs temporal-worker-workflow | grep "polling"
```

## Running the E2E Test

Once the environment is up and running, execute the automated E2E test script:

```bash
# Ensure you have your python environment activated with pytest installed
pytest scripts/test_temporal_e2e.py -v
```

This script will:
1. Submit a new task to the MoonMind API.
2. Monitor the task's progress as it is processed by the Temporal workflow.
3. Validate that the task reaches the 'success' state.
4. Verify that artifacts (like `plan.md`) were generated and are accessible.

## Teardown and Cleanup

To completely reset your local environment and clear out all Temporal execution history and artifacts:

```bash
docker compose down -v
```

This will stop all containers and remove the volumes associated with them (including the PostgreSQL databases and MinIO storage), ensuring a clean slate for your next run.
