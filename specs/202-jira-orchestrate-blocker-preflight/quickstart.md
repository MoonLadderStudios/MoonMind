# Quickstart: Jira Orchestrate Blocker Preflight

## Red-First Validation

1. Add or update a focused unit test for `jira-orchestrate` expansion in `tests/unit/api/test_task_step_templates_service.py`.
2. Assert the expanded preset includes a blocker preflight step immediately after `Move Jira issue to In Progress`.
3. Assert the preflight step instructions include the selected issue key, trusted Jira fetching, blocker-link inspection, Done-only success semantics, and fail-closed behavior for unavailable blocker status.
4. Assert the MoonSpec lifecycle, pull request handoff, and final Code Review transition remain after the preflight step.
5. Run the focused unit test and confirm it fails before implementation:

```bash
pytest tests/unit/api/test_task_step_templates_service.py -q
```

## Implementation Validation

After updating the seeded preset, rerun:

```bash
pytest tests/unit/api/test_task_step_templates_service.py -q
```

Expected result: the catalog expansion test passes with the new blocker preflight step and updated step count.

## Startup Seed Integration

Update startup seed coverage in `tests/integration/test_startup_task_template_seeding.py` so it verifies the seeded `jira-orchestrate` template includes the preflight step in the persisted catalog.

Run:

```bash
pytest tests/integration/test_startup_task_template_seeding.py -q
```

Expected result: startup seeding persists the updated Jira Orchestrate step sequence.

## Full Unit Verification

Run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: the required unit suite passes. If unrelated environment prerequisites block the run, record the exact blocker in verification output.

## Hermetic Integration Verification

When Docker is available, run:

```bash
./tools/test_integration.sh
```

Expected result: required `integration_ci` tests pass. In managed-agent containers without Docker socket access, record the exact Docker blocker and keep the focused startup seed integration result as local evidence.

## End-to-End Story Check

Before `/speckit.verify`, confirm the implementation satisfies these operator-visible cases:

- Target issue has a non-Done blocker: Jira Orchestrate stops before MoonSpec implementation, pull request creation, and Code Review transition.
- Target issue has only Done blockers: Jira Orchestrate continues through the existing lifecycle.
- Target issue has no blockers: Jira Orchestrate continues through the existing lifecycle.
- A blocker relationship exists but blocker status is unavailable: Jira Orchestrate stops as blocked and reports the target issue and available blocker details.
