# Quickstart: Agent Runtime Phase 1 Contracts

## 1. Prerequisites

- Repository checked out on branch `072-agent-run-contracts`.
- WSL/Docker test path available for unit tests.

## 2. Validate Contract Model and Adapter Changes

Run repository-standard unit tests:

```bash
./tools/test_unit.sh
```

## 3. Focused Verification (Optional During Iteration)

```bash
./tools/test_unit.sh tests/unit/schemas/test_agent_runtime_models.py
./tools/test_unit.sh tests/unit/workflows/adapters/test_jules_agent_adapter.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_activity_runtime.py
```

## 4. Expected Outcomes

- Canonical agent runtime contracts validate both success and failure payloads.
- Jules external adapter conforms to shared `AgentAdapter` behavior and normalized status/result mapping.
- Idempotent repeated starts with the same key do not trigger duplicate provider starts.
- Existing Temporal activity runtime tests continue passing with contract integration updates.
