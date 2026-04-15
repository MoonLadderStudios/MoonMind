# Quickstart: Temporal Payload Policy

1. Run focused schema tests:

   ```bash
   pytest tests/schemas/test_temporal_payload_policy.py tests/schemas/test_temporal_activity_models.py -q
   ```

2. Run the required unit suite:

   ```bash
   ./tools/test_unit.sh
   ```

3. Confirm compact refs still serialize in managed-session and runtime models while raw bytes or large text in metadata/provider summaries fail validation.
