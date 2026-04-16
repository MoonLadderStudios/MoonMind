# Quickstart: Claude Decision Pipeline

## Focused Red/Green Loop

1. Run the focused tests before implementation and confirm they fail:

   ```bash
   pytest tests/unit/schemas/test_claude_managed_session_models.py tests/integration/schemas/test_claude_decision_pipeline_boundary.py -q
   ```

2. Implement Claude DecisionPoint and HookAudit models in `moonmind/schemas/managed_session_models.py`.

3. Run the focused tests again:

   ```bash
   pytest tests/unit/schemas/test_claude_managed_session_models.py tests/integration/schemas/test_claude_decision_pipeline_boundary.py -q
   ```

4. Run existing managed-session model tests:

   ```bash
   pytest tests/unit/schemas/test_managed_session_models.py -q
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

- All documented decision stages validate and preserve canonical order.
- All documented decision event names validate.
- Protected-path decisions cannot be serialized as automatic allow decisions.
- Classifier decisions are distinct from user, policy, sandbox, hook, and runtime decisions.
- Headless unresolved decisions deny or defer only.
- HookAudit records validate documented source scopes, outcomes, and compact audit data.
