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
