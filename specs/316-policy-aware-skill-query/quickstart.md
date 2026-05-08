# Quickstart: Policy-Aware Skill Query

## Focused Unit Tests

Run the focused service and activity-boundary tests:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py
```

Expected result: Skills On Demand query tests pass for disabled behavior, enabled metadata-only results, invalid input, ineligible source diagnostics, active snapshot membership, bounded result counts, compact metadata, and no materialization.

## Activity Boundary Validation

Run the activity-boundary scenario through the same focused file:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py::test_enabled_activity_query_returns_typed_result
```

Expected result: the Temporal activity wrapper invokes the enabled query contract and does not call materialization.

## Story Validation

Run the focused story validation subset:

```bash
./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/services/test_skill_resolution.py
```

Expected result: Skills On Demand query behavior and resolver catalog behavior remain compatible.

## Full Unit Verification

Before final verification, run:

```bash
./tools/test_unit.sh
```

Expected result: the full Python unit suite and frontend Vitest suite pass through the repository test runner.

## Hermetic Integration Verification

If implementation later touches worker routing or activity catalog registration beyond the existing activity, run:

```bash
./tools/test_integration.sh
```

Current MM-613 scope is covered by unit and Temporal activity-boundary tests; no compose-backed integration change is required by the current implementation.

## Final MoonSpec Verification

After implementation and tests are complete, run the final `/moonspec-verify` equivalent against:

```text
specs/316-policy-aware-skill-query/spec.md
```

Expected result: verification preserves `MM-613`, the canonical Jira preset brief, and DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-013, and DESIGN-REQ-014 traceability.
