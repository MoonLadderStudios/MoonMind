# Quickstart: Workflow Docker Access Setting

## Unit Strategy

1. Verify the setting default and override behavior:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/config/test_settings.py
   ```

2. Verify Docker-backed workflow tools fail fast when disabled and continue when enabled:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal/test_workload_run_activity.py
   ```

3. Run final unit verification:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
   ```

## Integration Strategy

4. Verify the curated integration-CI tool contract routes through the workload boundary without requiring a live Docker daemon:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/integration/temporal/test_integration_ci_tool_contract.py
   ```

5. When Docker is available, run the full hermetic integration suite through the normal integration runner:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_integration.sh
   ```

6. Run `/moonspec-verify` against MM-476 artifacts and confirm `MM-476` remains present in spec, implementation notes, verification output, commit text, and pull request metadata.
