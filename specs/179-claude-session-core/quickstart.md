# Quickstart: Claude Session Core

## Focused Red/Green Loop

1. Run the focused unit tests before implementation and confirm they fail:

   ```bash
   pytest tests/unit/schemas/test_claude_managed_session_models.py -q
   ```

2. Implement the Claude core managed-session models in `moonmind/schemas/managed_session_models.py`.

3. Run the focused unit tests again:

   ```bash
   pytest tests/unit/schemas/test_claude_managed_session_models.py -q
   ```

4. Run the integration-style schema boundary test:

   ```bash
   pytest tests/integration/schemas/test_claude_managed_session_boundary.py -q
   ```

## Required Final Verification

Run the unit suite through the project runner:

```bash
./tools/test_unit.sh
```

Run hermetic integration tests when Docker is available in the environment:

```bash
./tools/test_integration.sh
```

## Expected Evidence

- Local, Remote Control, cloud interactive, cloud scheduled, desktop scheduled, SDK-hosted, and cloud handoff session shapes validate.
- Remote Control projection keeps the original execution owner.
- Cloud handoff creates a distinct destination session with lineage.
- Invalid lifecycle values fail validation.
- Claude payloads with `threadId`, `thread_id`, `childThread`, or `child_thread` fail validation.
- Existing Codex managed-session model tests still pass.
