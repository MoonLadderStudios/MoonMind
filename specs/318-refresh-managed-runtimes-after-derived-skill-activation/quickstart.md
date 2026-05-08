# Quickstart: Refresh Managed Runtimes After Derived Skill Activation

## Goal

Validate MM-615 with tests before implementation changes, then verify the final behavior through the canonical MoonMind test runners.

## Unit Test Strategy

Start with focused pytest iteration for the affected boundaries:

```bash
pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py -q
```

Required unit coverage:
- Manifest and content checksum verification succeeds before activation.
- Checksum mismatch returns `materialization_failed` and preserves the active snapshot.
- Partial writes or failed extraction do not expose a partial `.agents/skills` projection.
- Activation result metadata remains compact and excludes Skill bodies, hidden content, secrets, and unrestricted body-readable refs.
- Non-atomic projection support produces next-turn or controlled-steer-point activation guidance.
- External-agent activation exposure remains unavailable in v1.
- `materialization_failed` and `runtime_refresh_failed` diagnostics are distinguishable.

Final unit verification must use:

```bash
./tools/test_unit.sh
```

## Integration Test Strategy

Add or update hermetic integration coverage around the Temporal activity boundary:

```bash
pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -m integration_ci -q
```

Required integration coverage:
- `agent_skill.request_on_demand` returns `activated` only after materialization and verification succeed.
- Materialization failure preserves `active_snapshot_id`, clears derived refs, and emits `materialization_failed`.
- Runtime refresh/update failure preserves `active_snapshot_id`, clears activation refs, and emits `runtime_refresh_failed`.
- Adapter-visible active Skill projection is switched atomically where supported or deferred with explicit timing guidance.

Final integration verification must use:

```bash
./tools/test_integration.sh
```

## End-to-End Story Validation

1. Enable Skills On Demand in a hermetic activity test.
2. Provide an active snapshot and a requested Skill that resolves to a derived snapshot.
3. Verify materialization writes a complete manifest and validated backing store before activation output is accepted.
4. Verify the runtime activation result contains compact activation summary, materialization status, visible path or deferred activation guidance, and no large Skill bodies.
5. Simulate materialization and runtime refresh failures separately and confirm the previous snapshot remains active.
6. Confirm MM-615 and the canonical Jira preset brief remain preserved in `spec.md`, `plan.md`, `research.md`, `data-model.md`, contract, quickstart, tasks, and final verification.
