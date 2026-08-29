# Settings Configuration Pages: Migration Handoff

**Status:** temporary execution scaffolding. Delete this file when the migration
is complete.

This is the implementation checklist for the desired state defined in
`docs/UI/SettingsPage.md`. It names current code symbols and component names on
purpose, so it goes stale as soon as the migration lands. The canonical document
keeps only the durable route, ownership, and permission invariants; it must not
absorb the steps below.

Related: `docs/UI/SettingsPage.md`, `docs/UI/ProviderProfileCreation.md`,
`docs/Security/SettingsSystem.md`.

---

## 1. Destination registry

Replace the single Settings destination with:

```text
settings-providers-secrets -> /settings/providers-secrets
settings-user-workspace    -> /settings/user-workspace
settings-operations        -> /settings/operations
```

All three belong to the Configuration group. Local and server-provided destination registries must stay synchronized. Group membership should be explicit metadata or stable grouping logic.

## 2. Page boundaries

Recommended decomposition:

```text
ProvidersSecretsSettingsPage
  ProvidersSecretsHeader
  ProvidersSecretsHealthSummary
  ProviderProfilesManager
  SecretManager
  OAuth and token validation surfaces

UserWorkspaceSettingsPage
  UserWorkspaceHeader
  UserWorkspaceStatusSummary
  ScopeSwitcher
  GeneratedSettingsSection
  User and workspace identity/preferences surfaces

OperationsSettingsPage
  OperationsHeader
  OperationsStatusSummary
  OperationsSettingsSection
  Operational audit and diagnostics
```

The pages may initially share one bundle. They must still be route-owned components and must not mount unrelated managers.

## 3. Remove old section state

Remove equivalents of:

- `SETTINGS_SECTIONS`;
- `SettingsSectionId`;
- `readSectionFromLocation`;
- `updateSectionInLocation`;
- local cross-page `section` state;
- manual `popstate` handling for section selection;
- the destination radio group or segmented control;
- descriptions selected from a local section array; and
- conditional page rendering selected by `section === ...`.

Normal router matching replaces this state.

Move state to the owning page. Runtime filtering stays on Providers & Secrets. User versus workspace scope stays on User / Workspace. Worker command state stays on Operations.

## 4. Provider Profile form migration

Refactor the current form into:

```text
ProviderProfileForm
  StandardProfileFields
  ProviderProfileAuthenticationSetup
  ProviderProfileTierSection
  MaxParallelRunsField
  ProviderProfileAdvancedToggle
  ProviderProfileAdvancedRegion
```

Migration rules:

1. Move Account label into the standard identity group.
2. Keep credential connection status and setup actions visible.
3. Derive credential source and materialization mode from backend capabilities for guided paths.
4. Move Credentials & Volumes behind advanced options.
5. Move cooldown and rate-limit policy behind advanced options.
6. Keep max parallel runs visible.
7. Keep command behavior, tags, and priority advanced.
8. Replace editable Clear env keys with backend-generated read-only launch-safety metadata for normal profiles.
9. Remove unconditional client-side enablement from creation.
10. Preserve and reveal non-default or invalid advanced fields during edit.

## 5. Required tests

Cover:

- three destination-registry entries;
- one Configuration label and correct ordering;
- stable Settings trigger behavior;
- active state for all three routes;
- desktop and mobile navigation;
- `/settings` resolving to the first authorized destination, including when
  Providers & Secrets is hidden or unavailable, and the unavailable state when
  no destination is accessible;
- `/secrets` and `/workers` path redirects;
- `?section=` selecting nothing on a canonical route;
- absence of Settings tab or radio navigation;
- page-specific data loading and no unrelated manager mounting;
- Back and Forward behavior;
- dirty-draft route guards;
- direct-route permissions;
- deep links with page-local filters;
- collapsed Provider Profile advanced options on create;
- visible max parallel runs;
- backend-owned creation presets and activation;
- automatic advanced expansion for hidden errors; and
- preservation of advanced drafts while collapsed.

---

## 6. Retire `?section=` routing

The canonical document forbids `?section=` as page identity and defines no
redirect for it, so this migration completes the rename rather than aliasing it.
In the same change:

1. update every internal caller that builds a `?section=` Settings link,
   including `frontend/src/entrypoints/dashboard-app.tsx` and
   `frontend/src/entrypoints/dashboard-alerts.tsx`;
2. update `frontend/src/entrypoints/settings.test.tsx` and any other test that
   navigates by `?section=`;
3. delete the client-side section parsing and writing helpers; and
4. re-run a repo-wide search for `settings?section=` and confirm no caller,
   test, fixture, or document remains.

The backend `section=` catalog classification on
`/api/v1/settings/catalog` is unrelated and stays.
