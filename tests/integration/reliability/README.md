# Escaped-failure reliability journeys

Every escaped production reliability incident must add or minimize a fixture
under `tests/integration/reliability/replays/<failure-shape-id>/` before closure. A replay
contains a manifest with the incident reference, runtime/protocol metadata,
deterministic event script, workspace artifact manifest, redacted transcript
when relevant, and expected invariant/classification. Fixtures must require no
external network or credentials and must not contain secrets or raw production
logs.

Run the corpus with:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/integration/reliability \
  -m reliability_journey -q --durations=25
```

Run only the source-destroying archive replay with:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest \
  tests/integration/reliability/test_escaped_failure_journeys.py \
  -k source_destroying_cold_resume -q
```

The cold-resume replay crosses the production archive capture, artifact store,
source disposal, archive restore, filesystem-safety, and restore-idempotency
boundaries. Its fixture is retained under
`replays/cold-resume-worktree-archive/`. The complete required production
journey additionally needs the Temporal test server, UserWorkflow recovery
creation, managed AgentRun owner, and continuation ledger; a sandbox archive
replay is not evidence for those managed-runtime boundaries. The CI budget for
the complete reliability corpus is 30 minutes.

The suite complements focused tests. Replays should cross the production
adapter, terminal-evidence, activity-routing, or finalization boundary that let
the incident escape, and failure messages should name the violated invariant.
Bounded-continuation journeys drive AgentRun's terminal-contract owner directly
and cross the production activity route (asserting the managed agent-runtime task
queue) instead of standing up a time-skipping Temporal server, which keeps them
inside the hermetic reliability-journey budget while still asserting stable
session/thread/epoch identity across each continuation turn. Finalization faults
use the shared fail-first injector so checkpoint or publication retries can be
tested independently from the exactly-once primary agent execution.

## Programmable fault-injection model suite (#3709)

The `omnigent-fault-*` replay directories are generated from the programmable
fault-injection model suite in `moonmind/omnigent/faultkit/`. Instead of a
per-incident bespoke branch, each escaped incident is lifted into a generalized
invariant plus a minimized declarative `moonmind.omnigent-fault-scenario/v1`
scenario (stored here as `manifest.json` + `scenario.yaml`). The suite generates
thousands of unseen lifecycle interleavings from a seed, enforces twelve named
reliability invariants against both a reconciler-under-test and an independent
reference model, and minimizes any failing sequence into a safe replay fixture.

* Design: `docs/Omnigent/FaultInjectionReliabilitySuite.md`.
* Fast generated domain tests: `tests/unit/omnigent/faultkit/`.
* Hermetic reliability journey (fixed + incident corpus, with diagnostic
  bundles): `test_omnigent_fault_model_journey.py`.
* CI corpus policy and budgets: `moonmind/omnigent/faultkit/ci_policy.py`.
