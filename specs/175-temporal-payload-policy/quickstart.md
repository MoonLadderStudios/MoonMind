# Quickstart: Temporal Payload Policy

1. Run focused schema tests:

   ```bash
   pytest tests/schemas/test_temporal_payload_policy.py tests/schemas/test_temporal_activity_models.py -q
   ```

2. Run the required unit suite:

   ```bash
   ./tools/test_unit.sh
   ```

3. Integration strategy:

   ```bash
   ./tools/test_integration.sh
   ```

   This story does not require a new integration fixture when implementation remains limited to schema-boundary validation. Run the hermetic integration suite if changes expand into workflow/activity invocation wiring or other compose-backed Temporal boundaries.

4. Confirm compact refs still serialize in managed-session and runtime models while raw bytes or large text in metadata/provider summaries fail validation.
