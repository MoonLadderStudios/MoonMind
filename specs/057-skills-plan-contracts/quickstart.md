# Quickstart: Skills and Plans Runtime Contracts

## 1. Confirm runtime-mode scope

- Read `specs/045-skills-plan-contracts/spec.md`.
- Treat this feature as runtime implementation mode: production code plus validation tests are required.
- Do not consider docs/spec-only changes as completion for this feature.

## 2. Review implementation surfaces

- Contracts and runtime:
  - `moonmind/workflows/skills/tool_plan_contracts.py`
  - `moonmind/workflows/skills/tool_registry.py`
  - `moonmind/workflows/skills/plan_validation.py`
  - `moonmind/workflows/skills/tool_dispatcher.py`
  - `moonmind/workflows/skills/plan_interpreter.py`
  - `moonmind/workflows/skills/artifact_store.py`
- Integration wrapper:
  - `moonmind/workflows/skills/registry.py`
- Validation suites:
  - `tests/unit/workflows/test_skill_plan_runtime.py`
  - `tests/unit/workflows/test_skills_registry.py`
  - `tests/unit/workflows/test_skills_runner.py`

## 3. Validate contract flow locally

1. Create/parse skill registry payload and snapshot.
2. Build a plan payload pinned to snapshot digest + artifact reference.
3. Validate plan structurally and through deep validation activity.
4. Execute interpreter with dependency-aware scheduling and policy behavior.
5. Inspect progress and summary payload/artifact references.

## 4. Run repository-standard unit tests

```bash
./tools/test_unit.sh
```

Notes:

- This repository requires `./tools/test_unit.sh` for unit test acceptance.
- Do not replace this with direct `pytest`.
- In WSL, the script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1`.

## 5. Spot-check runtime contract expectations

- Invalid registry definitions fail before execution.
- Invalid plans (cycles, missing skills, bad refs, bad schema inputs) fail before interpreter run.
- `FAIL_FAST` cancels outstanding work on first failure.
- `CONTINUE` allows independent branches to complete.
- Progress query returns structured node counts and timestamps.
- Summary includes per-node results/failures and artifact refs for large outputs.

## 6. Artifact completion gate for planning phase

- `specs/045-skills-plan-contracts/plan.md`
- `specs/045-skills-plan-contracts/research.md`
- `specs/045-skills-plan-contracts/data-model.md`
- `specs/045-skills-plan-contracts/quickstart.md`
- `specs/045-skills-plan-contracts/contracts/skills-plan-runtime.openapi.yaml`
- `specs/045-skills-plan-contracts/contracts/requirements-traceability.md`
