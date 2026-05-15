# Quickstart: Unit Death And Ragdoll

This quickstart is for the target THOR Tactics gameplay workspace. The current MoonMind workspace does not contain the THOR source, so these commands must be adapted to the target repository's actual project file, build scripts, and test runner.

## Prerequisites

- Open the target THOR Tactics repository or workspace that contains unit, combat, and presentation systems.
- Confirm the project file, engine version, and available local or CI test commands.
- Preserve Jira issue `THOR-407` and original preset brief `THOR-407: Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md` in task and verification evidence.

## Unit Test Strategy

Write failing unit tests before implementation for:

- Lethal damage changes one living unit to one dead state.
- Overkill damage still produces exactly one death transition.
- Repeated post-death damage, delayed effects, collisions, and duplicate notifications do not replay gameplay-side death effects.
- Dead units fail living-unit action, turn, and targeting predicates.
- Cleanup unregisters or invalidates active-unit references.
- Non-ragdoll units choose an equivalent visible fallback presentation.

Target command:

```bash
# Replace with the target THOR unit test command once the workspace is available.
./tools/test_unit.sh --filter UnitDeathAndRagdoll
```

## Integration Test Strategy

Write failing integration or automation tests before implementation for:

- A normal gameplay attack defeats a unit and removes it from active combat flow.
- Death presentation starts for a ragdoll-capable unit.
- Fallback death presentation starts for a non-ragdoll-capable unit.
- Turn order, targeting, victory or encounter completion, and cleanup all observe the dead state.
- Encounter cleanup after final death leaves no dangling active-unit references.

Target command:

```bash
# Replace with the target THOR integration or automation command once the workspace is available.
./tools/test_integration.sh --filter UnitDeathAndRagdoll
```

## End-To-End Validation

1. Run the unit tests and confirm they fail for missing behavior before production changes.
2. Run the integration tests and confirm they fail for missing combat-flow or presentation behavior before production changes.
3. Implement the smallest target-project changes needed to satisfy the tests.
4. Re-run the focused unit tests until they pass.
5. Re-run the focused integration tests until they pass.
6. Record exact commands, exit codes, and relevant gameplay log lines in the implementation evidence.
7. Confirm final verification preserves `THOR-407` and the original preset brief.

## Current Workspace Blocker

This managed run is operating in the MoonMind repository, not the THOR Tactics gameplay repository. No runtime implementation or gameplay test execution can be completed here until the target source is available.

Implementation attempt evidence:

- Checklist gate passed: `specs/358-unit-death-ragdoll/checklists/requirements.md` has 21 completed items and 0 incomplete items.
- MoonSpec prerequisite script could not resolve the active feature because the managed branch is `run-jira-orchestrate-for-thor-407-5bc400d9`, not an `NNN-feature-name` branch.
- Target workspace check found no `.uproject`, no `Source/ThorTactics`, no `Source/ThorTactics/Units/UnitDeathState.h`, no `Source/ThorTactics/Tests/Units/UnitDeathStateTests.cpp`, and no `Docs/TacticsUnits_Core/UnitDeathAndRagdoll.md` in this checkout.
- `tasks.md` task T001 remains incomplete because there is no target THOR gameplay workspace path to record.
