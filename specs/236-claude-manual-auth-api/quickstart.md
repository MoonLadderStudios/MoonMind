# Quickstart: Claude Manual Auth API

## Scope

Validate MM-447: Mission Control can submit a Claude Anthropic token to a dedicated manual-auth backend path, store token material only in Managed Secrets, update the provider profile to a secret-reference launch shape, sync runtime-visible profile state, and return only secret-free readiness metadata.

## Focused Unit/API Verification

Run the provider profile route tests:

```bash
./tools/test_unit.sh tests/unit/api_service/api/routers/test_provider_profiles.py
```

Expected evidence:
- Successful manual-auth commit returns readiness metadata without the submitted token.
- The provider profile after commit uses `secret_ref` and `api_key_env`.
- Volume-backed fields are cleared.
- Managed Secret storage contains the token while responses and profile payloads do not.
- Malformed token failure returns a generic secret-free validation error and does not persist a ready binding.

Run the secret resolver tests:

```bash
./tools/test_unit.sh tests/unit/workflows/adapters/test_secret_redaction.py
```

Expected evidence:
- Legacy UUID secret references remain supported.
- `db://` slug references resolve to the managed secret value at the resolver boundary.
- Invalid references do not trigger database reads.

## Full Required Verification

Run the full required unit suite:

```bash
./tools/test_unit.sh
```

Expected evidence:
- Python unit tests pass.
- Frontend unit tests pass.
- No raw submitted token appears in route responses, profile responses, or failure text covered by MM-447 tests.

## End-to-End Manual Scenario

1. Start MoonMind normally.
2. Ensure a `claude_code` / `anthropic` provider profile exists.
3. Submit a token to `POST /api/v1/provider-profiles/{profile_id}/manual-auth/commit`.
4. Fetch the provider profile.
5. Confirm the profile contains only secret references and readiness metadata.
6. Confirm runtime secret resolution can resolve the `db://` reference when materializing a launch profile.

## Final MoonSpec Verification

Run `/speckit.verify` or the equivalent `moonspec-verify` workflow for:

```text
specs/236-claude-manual-auth-api/spec.md
```

Expected verdict: `FULLY_IMPLEMENTED` when current code and tests satisfy all MM-447 requirements.
