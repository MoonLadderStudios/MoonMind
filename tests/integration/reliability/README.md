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
pytest tests/integration/reliability -m reliability_journey -q
```

The suite complements focused tests. Replays should cross the production
adapter, terminal-evidence, activity-routing, or finalization boundary that let
the incident escape, and failure messages should name the violated invariant.
Bounded-continuation journeys drive AgentRun's terminal-contract owner directly
and cross the production activity route (asserting the managed agent-runtime task
queue) instead of standing up a time-skipping Temporal server, which keeps them
inside the hermetic `integration_ci` budget while still asserting stable
session/thread/epoch identity across each continuation turn. Finalization faults
use the shared fail-first injector so checkpoint or publication retries can be
tested independently from the exactly-once primary agent execution.
