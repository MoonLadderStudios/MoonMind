---
name: pre-existing-temporal-worker-runtime-failures
description: tests/unit/workflows/temporal/test_temporal_worker_runtime.py runtime_planner tests fail pre-existing with KeyError on step dict keys, unrelated to changes
metadata:
  type: reference
---

`tests/unit/workflows/temporal/test_temporal_worker_runtime.py::test_runtime_planner_*`
tests fail with `KeyError` on step dict keys (`'type'`, `'source'`, `'storyOutput'`,
`'storyBreakdownPath'`, `'targetBranch'`, `'jiraOrchestration'`). Confirmed reproducing
on a clean tree (changes stashed) in this managed-agent workspace — ~12 failures.
They exercise the runtime planner's step-materialization paths and do NOT load the
real seed preset catalog (no `data/presets` / `sync_seed_templates` reference), so
preset YAML additions cannot cause them.

**Why:** A focused run that bundles these tests looks alarming during verification,
but they are unrelated to seed-catalog/preset work. Likely a `tests/helpers/step_type_payloads`
fixture or planner-contract drift artifact.

**How to apply:** Before treating a `test_temporal_worker_runtime.py` runtime_planner
KeyError as a regression, reproduce it with your changes stashed (`git stash -u`). If
it reproduces there too, it is pre-existing and out of scope. Related: [[pre-existing-test-executions-failures]].
