# Quickstart: Normalize Proposal Intent in Temporal Submissions

## Prerequisites

- Python 3.12 environment with repo dependencies installed.
- Use `MOONMIND_FORCE_LOCAL_TESTS=1` in managed-agent local test mode.
- No external Jira, GitHub, or provider credentials are required for the planned unit and hermetic integration tests.

## Unit Test Strategy

Run targeted tests while implementing:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_proposals.py
./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py
```

Required unit evidence:

- API task-shaped submission persists `task.proposeTasks` and `task.proposalPolicy` without root-level proposal intent.
- Invalid `task.proposalPolicy` still fails with field-addressable validation.
- Temporal proposal stage enters only when global enablement and canonical nested opt-in are both true.
- Temporal compatibility test covers previous root-only proposal opt-in without making it a new write contract.
- Codex managed-session task proposal checks read canonical nested task intent, not adapter-local or environment state.
- Mission Control status mapping keeps `proposals` vocabulary consistent.

## Integration Test Strategy

Run hermetic integration validation before final verification:

```bash
./tools/test_integration.sh
```

Targeted integration evidence, if local iteration needs a narrower command:

```bash
pytest tests/integration/workflows/temporal/workflows/test_run.py -k proposals -q --tb=short
```

Required integration evidence:

- A workflow run with canonical nested proposal opt-in invokes proposal activities and reports proposal counts.
- A workflow run without canonical nested proposal opt-in skips proposal activities.
- Proposal-stage finish summary fields remain durable and redaction-safe.

## End-to-End Story Check

1. Submit representative task-shaped payloads for API, managed-runtime originated creation, proposal promotion, and scheduled creation where applicable.
2. Confirm each new run stores proposal intent only under `initialParameters.task`.
3. Confirm workflow proposal gating uses canonical nested task opt-in plus global enablement.
4. Confirm replay or compatibility tests cover older root-only payloads.
5. Confirm `proposals` appears consistently in workflow/API/UI/summary surfaces touched by the implementation.
6. Confirm MM-595 and DESIGN-REQ-003 through DESIGN-REQ-006 remain referenced in implementation evidence and verification output.
