# Quickstart: Settings Operations Deployment Update UI

1. Open `/tasks/settings?section=operations`.
2. Confirm the Deployment Update card appears under Operations.
3. Confirm current stack, Compose project, configured image, service health, and last update context render.
4. Select `latest` and verify a mutable-tag warning appears.
5. Enter a reason and submit; verify confirmation includes current image, target image, mode, stack, affected services, mutable warning, and restart warning.
6. Confirm the request posts to `/api/v1/operations/deployment/update`.

## Validation Commands

```bash
npm run ui:test -- frontend/src/components/settings/OperationsSettingsSection.test.tsx
./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx
rg -n "MM-522|DESIGN-REQ-001|DESIGN-REQ-002|DESIGN-REQ-016|DESIGN-REQ-017" specs/264-settings-operations-deployment-update-ui
```
