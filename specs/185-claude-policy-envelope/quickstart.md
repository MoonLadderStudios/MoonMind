# Quickstart: Claude Policy Envelope

Source issue: `MM-343`

## Focused Red-First Checks

Run focused unit tests before production changes:

```bash
pytest tests/unit/schemas/test_claude_policy_envelope.py -q
```

Expected red-first result: tests fail because `ClaudePolicyEnvelope`, `ClaudePolicySource`, `ClaudePolicyHandshake`, `ClaudePolicyEvent`, and `resolve_claude_policy_envelope` are not implemented yet.

Run focused integration-style boundary tests before production changes:

```bash
pytest tests/integration/schemas/test_claude_policy_envelope_boundary.py -q
```

Expected red-first result: tests fail because the public schema boundary does not yet expose the MM-343 policy envelope behavior.

## Implementation Verification

After implementation, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_claude_policy_envelope.py
```

Then run the focused integration-style test directly:

```bash
pytest tests/integration/schemas/test_claude_policy_envelope_boundary.py -q
```

When Docker is available, run the required hermetic integration suite:

```bash
./tools/test_integration.sh
```

## End-To-End Story Validation

1. Build fixture policy sources for server-managed, endpoint-managed, empty, cache-hit, fetch-failed, fail-closed, interactive-dialog, non-interactive blocked, and BootstrapPreferences scenarios.
2. Resolve each fixture through `resolve_claude_policy_envelope`.
3. Verify the resulting envelope, handshake, events, and serialized aliases preserve `MM-343` requirements:
   - managed source precedence is deterministic,
   - lower scopes are observability-only,
   - fail-closed does not produce permissive startup behavior,
   - risky managed controls require dialog or block when non-interactive,
   - BootstrapPreferences are bootstrap templates only,
   - provider mode and policy trust level are recorded on every successful envelope.
