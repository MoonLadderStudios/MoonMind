# Quickstart: Validate Session Workflow Hardening

## Prerequisites

- Run commands from the repository root.
- Use the active feature branch: `157-harden-session-workflow`.
- Enable managed-agent local test mode for final unit verification:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

## Focused Workflow Validation

Run the focused workflow and schema tests:

```bash
python -m pytest -q tests/unit/workflows/temporal/workflows/test_agent_session.py
```

Expected result:

- Validators still reject stale epochs, missing active turns, duplicate clear, and post-termination mutations.
- Runtime-bound updates accepted before handles are attached wait until handles exist.
- Async mutators are serialized by the workflow-level lock.
- Completion waits for accepted handlers to finish.
- Continue-As-New carries bounded session identity, locator, control metadata, continuity refs, and request-tracking state.

## Repository Unit Verification

Run the standard unit wrapper before finalizing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result:

- Python unit tests pass.
- Frontend unit tests invoked by the wrapper pass.
- No nested Docker fallback is required in managed-agent worker environments.

## Optional Temporal Runtime Smoke Check

When a local Temporal/Docker-backed MoonMind environment is available:

1. Start MoonMind with `docker compose up -d`.
2. Submit a Codex managed-session task.
3. Send a follow-up during or immediately after launch.
4. Confirm the accepted control waits for runtime handles instead of failing due to missing locator state.
5. Exercise interrupt or clear while another mutator is active and confirm query state remains coherent.
6. Trigger a low-threshold Continue-As-New in a test environment and confirm the next run preserves the same logical session identity and latest continuity refs.

This smoke check is optional for unit completion but useful before broad rollout.
