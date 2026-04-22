# Quickstart: Route Claude Auth Actions

## Focused Test-First Workflow

1. Prepare frontend dependencies if needed:

   ```bash
   npm ci --no-fund --no-audit
   ```

2. Add failing UI tests in `frontend/src/components/settings/ProviderProfilesManager.test.tsx`:

   ```bash
   npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx
   ```

   Expected before implementation: tests for Claude-specific row actions fail because only Codex `Auth` is currently implemented.

3. Implement row action classification in `frontend/src/components/settings/ProviderProfilesManager.tsx`.

4. Re-run focused UI tests:

   ```bash
   npm run ui:test -- frontend/src/components/settings/ProviderProfilesManager.test.tsx
   ```

5. Run final required unit verification:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
   ```

## End-To-End Story Check

Render Settings with:

- a disconnected `claude_anthropic` row carrying trusted Claude manual-token capability metadata,
- a connected `claude_anthropic` row carrying supported lifecycle actions,
- a `codex_default` or equivalent Codex OAuth-capable row,
- an unsupported row with missing Claude metadata.

The story passes when:

- disconnected Claude shows `Connect Claude`;
- connected Claude shows only supported `Replace token`, `Validate`, and `Disconnect` actions;
- Claude rows do not show generic Codex `Auth`;
- Codex OAuth still starts, finalizes, and retries through the existing OAuth session APIs;
- unsupported rows show no misleading Claude auth action;
- MM-445 and DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-007 remain traceable in final verification.
