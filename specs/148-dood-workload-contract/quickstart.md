# Quickstart: Docker-Out-of-Docker Workload Contract

## Focused Validation

Run the Phase 1 workload contract tests:

```bash
./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py
```

Run the existing Phase 0 documentation contract:

```bash
./tools/test_unit.sh tests/unit/docs/test_dood_phase0_contract.py
```

## Full Unit Validation

Run the complete unit suite before finalizing:

```bash
./tools/test_unit.sh
```

## Manual Contract Smoke Check

1. Load a valid JSON or YAML runner profile registry with one curated profile.
2. Construct a workload request using only the profile ID, task/step metadata, workspace paths under `/work/agent_jobs`, allowed environment overrides, and resource overrides within the profile maximum.
3. Confirm validation returns deterministic `moonmind.*` labels.
4. Change the request to use an unknown profile, a path outside `/work/agent_jobs`, a disallowed env key, or excessive memory.
5. Confirm validation rejects the request without invoking Docker.

Expected result: Phase 1 can validate workload intent and policy without Docker access, launcher code, or tool executor integration.
