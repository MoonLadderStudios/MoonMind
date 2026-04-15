# Quickstart: Temporal Type-Safety Gates

## Targeted TDD Loop

Start by writing failing tests for the gate contract:

```bash
pytest tests/unit/workflows/temporal/test_temporal_type_safety_gates.py -q
```

Add schema or payload-policy fixtures as needed:

```bash
pytest tests/unit/schemas/test_temporal_payload_policy.py \
  tests/unit/schemas/test_temporal_signal_contracts.py \
  tests/unit/schemas/test_managed_session_models.py -q
```

Add workflow-boundary or replay-style coverage for compatibility-sensitive cases:

```bash
pytest tests/integration/temporal/test_temporal_type_safety_gates.py -q
```

The integration test module should use the repo's hermetic integration markers, including `integration` and `integration_ci`, unless implementation discovers a concrete reason the fixture cannot run in required CI.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Verification

```bash
./tools/test_integration.sh
```

Run this when Docker is available. In managed agent workspaces where the Docker socket is unavailable, record that exact blocker and rely on targeted unit and workflow-boundary evidence for this planning stage.

## Expected Evidence

- Gate rule tests prove missing compatibility evidence, unsafe non-additive changes, and known anti-patterns fail with actionable findings.
- Safe additive compatibility fixtures pass with evidence.
- Escape-hatch tests prove only transitional, boundary-only, compatibility-justified shapes are accepted.
- Workflow-boundary or replay-style tests prove representative Temporal contract changes are covered before implementation proceeds.
