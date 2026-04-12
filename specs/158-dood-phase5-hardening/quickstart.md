# Quickstart: DooD Phase 5 Hardening

## Purpose

Validate that Docker-backed workload tools fail closed for unsafe input, enforce bounded capacity, clean expired orphans, and expose non-secret diagnostics.

## Prerequisites

- Python dependencies installed for unit tests.
- No Docker daemon is required for the focused unit path; launcher behavior is tested with process fakes.
- Use the repository test wrapper for final verification.

## Focused Validation

Run the workload contract and launcher tests:

```bash
pytest tests/unit/workloads -q
```

Run the adjacent Temporal activity and worker bootstrap tests:

```bash
pytest \
  tests/unit/workloads/test_workload_tool_bridge.py \
  tests/unit/workflows/temporal/test_workload_run_activity.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_build_agent_runtime_deps_uses_artifacts_env_without_double_nesting \
  -q
```

## Final Unit Verification

Run the canonical unit wrapper:

```bash
./tools/test_unit.sh
```

## Expected Evidence

- Unsafe image provenance, auth-volume inheritance, disallowed env keys, disallowed mounts, excessive resources, host networking, privileged posture, and implicit device access are rejected before launch.
- Workload tool failures include stable denial reasons and non-secret details.
- Docker run construction includes explicit no-privileged hardening.
- Per-profile and fleet-level workload limits prevent additional launches when capacity is exhausted.
- Expired workload-labeled containers are removed by cleanup while non-expired and unrelated containers are skipped.
- Workload artifacts and bounded metadata remain the source of truth; no session continuity artifact is created for a workload container.

## Rollback / Failure Notes

- If policy validation becomes too permissive, disable affected runner profiles by removing them from the deployment-owned registry.
- If fleet capacity limits are too low, adjust the operator-owned workload fleet limit and rerun capacity tests.
- If cleanup removes an unexpected container in tests, treat it as a release blocker because cleanup must require MoonMind workload ownership labels.
