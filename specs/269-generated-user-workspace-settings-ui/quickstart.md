# Quickstart: Generated User and Workspace Settings UI

1. Start Mission Control or run the frontend tests with API requests mocked.
2. Open `/settings?section=user-workspace`.
3. Confirm the UI requests `/api/v1/settings/catalog?section=user-workspace&scope=workspace`.
4. Confirm eligible descriptors render by category with source and scope badges.
5. Change an editable setting and confirm the preview lists only that changed key.
6. Save and confirm the UI sends `PATCH /api/v1/settings/workspace` with only changed keys and expected versions.
7. Reset an overridden setting and confirm the UI sends `DELETE /api/v1/settings/workspace/{key}` and refreshes descriptors.
8. Switch to user scope and confirm descriptors are reloaded for `scope=user`.
9. Confirm read-only rows are disabled with lock reasons.
10. Confirm SecretRef rows never display or request plaintext secret values.

Validation commands:

```bash
npm run ui:test -- frontend/src/components/settings/GeneratedSettingsSection.test.tsx
./tools/test_unit.sh tests/unit/api_service/api/routers/test_settings_api.py tests/unit/services/test_settings_catalog.py --ui-args frontend/src/components/settings/GeneratedSettingsSection.test.tsx
```
