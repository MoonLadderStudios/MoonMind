import { describe, expect, it } from 'vitest';

import {
  DASHBOARD_DESTINATION_GROUPS,
  DASHBOARD_DESTINATIONS,
  filterSettingsQueryForTarget,
  resolveDashboardRoute,
} from './dashboardRoutes';

// MoonLadderStudios/MoonMind#4353: the account-free instance has no
// MoonMind account setup, member administration, owner filters,
// role-dependent navigation, or personal/global catalog controls.
describe('account-free instance cutover', () => {
  it('exposes an Instance destination instead of User / Workspace', () => {
    const instance = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'settings-instance');
    expect(instance).toMatchObject({
      label: 'Instance',
      canonicalPath: '/settings/instance',
      capabilityKey: 'settingsInstance',
      page: 'settings-instance',
      menuGroupKey: 'configuration',
    });
    expect(instance?.pathPatterns).toEqual(['/settings/instance']);
  });

  it('keeps no user identity or human scope in destination metadata', () => {
    const serialized = JSON.stringify(DASHBOARD_DESTINATIONS);
    expect(serialized).not.toContain('settings-user-workspace');
    expect(serialized).not.toContain('User / Workspace');
    expect(serialized).not.toContain('settingsUserWorkspace');
    expect(serialized).not.toContain('/settings/user-workspace');
    const group = DASHBOARD_DESTINATION_GROUPS.find(({ key }) => key === 'configuration');
    expect(group?.destinationKeys).toEqual([
      'settings-providers-secrets',
      'settings-instance',
      'settings-operations',
    ]);
  });

  it('resolves the canonical Instance route and rejects the retired account path', () => {
    expect(resolveDashboardRoute('/settings/instance')).toEqual({
      page: 'settings-instance',
      dataWidePanel: true,
      currentPath: '/settings/instance',
    });
    expect(resolveDashboardRoute('/settings/user-workspace')).toBeNull();
  });

  it('drops stale human-scope params when targeting the Instance page', () => {
    expect(filterSettingsQueryForTarget('?scope=user&q=team&section=user-workspace', '/settings/instance'))
      .toBe('/settings/instance');
    expect(filterSettingsQueryForTarget('?scope=workspace', '/settings/instance'))
      .toBe('/settings/instance');
  });
});
