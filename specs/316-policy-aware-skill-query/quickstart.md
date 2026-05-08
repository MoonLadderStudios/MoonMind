# Quickstart: Policy-Aware Skill Query

## Focused Unit Tests

1. Add red-first tests for enabled query behavior:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py
```

Expected before implementation: enabled query tests fail because the service returns `enabled_mode_not_implemented` and no metadata results.

2. After implementation, the same command should pass and cover:
- disabled query still returns `feature_disabled` and no results;
- enabled valid query returns `ok` with bounded metadata;
- invalid or blank query returns structured denial before catalog results;
- metadata results omit body/content refs;
- ineligible matches are filtered or marked `eligible: false`;
- active snapshot membership sets `in_current_snapshot`.

## Activity Boundary Validation

Run the activity-boundary subset:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py::test_enabled_activity_query_returns_metadata_without_materializing_snapshot
```

Expected result: the Temporal activity wrapper invokes the same enabled query contract and does not call materialization.

## Full Unit Verification

Before final verification, run:

```bash
./tools/test_unit.sh
```

## Hermetic Integration Verification

If implementation touches worker routing or activity catalog registration beyond the existing activity, run:

```bash
./tools/test_integration.sh
```

## Final MoonSpec Verification

After tasks and implementation are complete, run the final `/moonspec-verify` equivalent against:

```text
specs/316-policy-aware-skill-query/spec.md
```

Expected result: verification preserves `MM-613`, the canonical Jira preset brief, and DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-013, and DESIGN-REQ-014 traceability.
