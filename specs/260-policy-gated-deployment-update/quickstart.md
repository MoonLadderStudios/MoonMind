# Quickstart: Policy-Gated Deployment Update API

## Test-First Commands

Focused unit/API-router validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_deployment_operations.py
```

Full unit validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Hermetic integration suite:

```bash
./tools/test_integration.sh
```

## Story Validation

1. Submit a valid administrator request to `POST /api/v1/operations/deployment/update`.
2. Confirm the response is `202` and includes `deploymentUpdateRunId`, `taskId` or `workflowId`, and `QUEUED`.
3. Submit the same request as a non-admin user and confirm `403 deployment_update_forbidden`.
4. Submit invalid policy values for stack, repository, mode, reference, and reason and confirm `422` responses with explicit error codes.
5. Submit payloads with `command`, `composeFile`, host-path-like, or runner-choice fields and confirm schema rejection.
6. Read `/api/v1/operations/deployment/stacks/moonmind` and `/api/v1/operations/deployment/image-targets?stack=moonmind` and confirm the typed response shapes.
