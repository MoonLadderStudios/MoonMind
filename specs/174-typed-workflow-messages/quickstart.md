# Quickstart: Typed Workflow Messages

1. Run the focused managed-session schema tests:

   ```bash
   pytest tests/unit/schemas/test_managed_session_models.py -q
   ```

2. Run the focused managed-session workflow tests:

   ```bash
   pytest tests/unit/workflows/temporal/workflows/test_agent_session.py -q
   ```

3. Run the lifecycle workflow scenario:

   ```bash
   pytest tests/integration/services/temporal/workflows/test_agent_session_lifecycle.py -q
   ```

4. Run the full unit suite before finalizing:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
   ```

Expected result: schema tests validate explicit contracts, workflow tests validate validators and Continue-As-New typing, and lifecycle tests exercise signal/update/query behavior.
