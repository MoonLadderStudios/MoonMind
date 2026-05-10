# Quickstart: Agent Tool-Surface Isolation

## Prerequisites

- Python 3.12 environment prepared for MoonMind development.
- Repo dependencies available through the standard unit/integration test scripts.
- No live Jira or GitHub credentials are required for required CI evidence; provider verification remains optional/manual.

## Unit Test Strategy

Run focused tests first while implementing:

```bash
./tools/test_unit.sh tests/unit/workflows/skills/test_tool_surface_contracts.py tests/unit/workflows/temporal/runtime/test_launcher_surface_contracts.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/workflows/adapters/test_github_service.py tests/unit/workflows/temporal/test_jules_activities.py tests/unit/workflows/temporal/test_publish_branch_lease.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/test_isolation_diagnostics.py tests/unit/specs/test_mm680_traceability.py
```

Expected additions:
- Skill surface contract schema and validation rejects undeclared tools, MCPs, connectors, egress rules, and publish authority.
- Managed runtime launcher rejects operator identity and undeclared runtime surfaces before startup.
- Runtime dispatch carries sanitized selected-skill and surface-contract metadata.
- GitHub PR service adopts existing head/base pull requests before creating.
- repo.create_pr activity and workflow handling accept adopted pull request results.
- Branch publish helper classifies lease misses as retryable structured conflicts.
- Direct publish denial and isolation diagnostics redact secret-like values.
- MM-680 traceability remains preserved across generated artifacts.

Final unit verification:

```bash
./tools/test_unit.sh
```

## Integration Test Strategy

Run hermetic integration CI tests after unit coverage is passing:

```bash
./tools/test_integration.sh
# Focused iteration, when needed:
pytest tests/integration/temporal/test_agent_runtime_surface_isolation.py tests/integration/temporal/test_mm680_original_incident.py tests/integration/temporal/test_publish_reconciliation.py tests/integration/temporal/test_runtime_parity_surface_contract.py -m integration_ci -q --tb=short
```

Expected integration_ci additions:
- Managed runtime launch with a disallowed connector or external-service destination fails before runtime startup.
- Agent runtime attempt to access a non-contract destination is blocked and emits a sanitized diagnostic.
- Direct in-session publish attempt has no usable credential/remote path and does not mutate external state.
- MoonMind-owned publish adopts an existing pull request for matching head/base.
- Branch publish lease miss returns a retryable structured conflict.

## End-to-End Story Validation

1. Prepare a managed agent task with a selected skill contract that allows only MoonMind trusted Jira and required repository read/write surfaces.
2. Launch the runtime with a simulated operator-account connector grant and verify startup is rejected with `identity_rejected` diagnostics.
3. Launch with a valid service identity and attempt a non-contract external-service call from inside the session; verify the call fails and records `egress_blocked`.
4. Attempt direct branch push or PR creation inside the agent session; verify no external mutation occurs and `direct_publish_denied` evidence is recorded.
5. Seed a pre-existing pull request for the intended head/base in the mocked provider service; verify `repo.create_pr` returns adopted success.
6. Simulate remote branch movement before publish; verify branch publish returns a retryable `lease_conflict` result.
7. Confirm `MM-680` remains present in `spec.md`, `plan.md`, `research.md`, `data-model.md`, contracts, `quickstart.md`, downstream `tasks.md`, and final verification evidence.

## Required Evidence Before Tasks Are Complete

- Unit tests pass through `./tools/test_unit.sh`.
- Hermetic integration tests pass through `./tools/test_integration.sh`, or exact environment blockers are documented.
- No raw credentials appear in diagnostics, logs, or artifacts.
- Requirement status in `tasks.md` maps every FR, scenario, SC, and in-scope DESIGN-REQ to tests and implementation or verification-only work.
- `/moonspec-verify` runs after implementation and tests pass, using the preserved MM-680 brief as the final alignment source.
