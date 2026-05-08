# Quickstart: Resolve On-Demand Skill Requests

## Test-First Validation

1. Add failing unit tests for enabled request validation, `no_change`, activation, failure preservation, compact serialization, and lineage:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py
```

2. Add a failing activity-boundary test for `agent_skill.request_on_demand` invoking resolver/materializer and returning compact activated metadata:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -q --tb=short
```

3. Implement the request contract, service, and activity behavior until the focused tests pass.

4. Run final unit verification:

```bash
./tools/test_unit.sh
```

5. Run hermetic integration verification when Docker is available:

```bash
./tools/test_integration.sh
```

## End-to-End Story Check

- Enable Skills On Demand.
- Start from an active snapshot containing one Skill.
- Request an already-active Skill and confirm `no_change`.
- Request one inactive policy-eligible Skill and confirm one derived snapshot, compact lineage, and activation guidance.
- Request invalid or denied Skills and confirm structured denial without parent snapshot mutation.
