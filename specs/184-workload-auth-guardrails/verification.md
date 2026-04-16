# Verification: Workload Auth-Volume Guardrails

## Scope

- Jira issue: MM-318
- Preserved preset brief: `MM-318: breakdown docs\ManagedAgents\OAuthTerminal.md`
- Story: STORY-006 Workload Auth-Volume Guardrails
- Source design: `docs/ManagedAgents/OAuthTerminal.md`

## Requirement Coverage

- FR-001, FR-002, FR-003: `RunnerProfile.credentialMounts` is the only approved path for auth-like workload volumes. Normal `requiredMounts` and `optionalMounts` still reject auth, credential, and secret volume names.
- FR-004: workload Docker labels and result metadata expose `identityKind=workload` for workload containers, keeping workload identity separate from managed-session identity.
- FR-005: MM-318 and the original preset brief remain present in `spec.md`, `tasks.md`, and this verification record.
- DESIGN-REQ-009: workloads do not inherit managed-runtime auth volumes by default; auth-like volumes require explicit credential mount declaration.
- DESIGN-REQ-010: credential mount justification and approval metadata are not emitted into Docker run args; existing output and metadata redaction remains in force.
- DESIGN-REQ-020: changes stay inside workload schema and Docker workload launcher boundaries.

## TDD Evidence

- Red unit: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py` failed before implementation on missing `credentialMounts` support and missing workload `identityKind` metadata.
- Red integration: `pytest tests/integration/services/temporal/workflows/test_agent_run.py -k workload_auth_volume_guardrails -q --tb=short` failed before implementation on missing `credentialMounts` support.
- Focused unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py` passed with 68 tests.
- Focused integration green: `pytest tests/integration/services/temporal/workflows/test_agent_run.py -m integration_ci -k workload_auth_volume_guardrails -q --tb=short` passed with 1 test.
- Full unit green: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3202 Python tests, 16 subtests, and 221 frontend tests.

## Integration Runner

`./tools/test_integration.sh` was attempted and blocked by the managed-agent environment: Docker was unavailable at `/var/run/docker.sock`.

## Result

PASS with one environment blocker: full compose-backed integration verification requires a Docker-enabled environment.
