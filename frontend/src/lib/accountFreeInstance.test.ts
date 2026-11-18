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
  it('exposes Security without introducing accounts', () => {
    const instance = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'security');
    expect(instance).toMatchObject({
      label: 'Security',
      canonicalPath: '/security',
      capabilityKey: 'settingsInstance',
      page: 'security',
      navigationGroup: 'primary',
    });
    expect(instance?.pathPatterns).toContain('/security/targets');
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
      'settings-operations',
    ]);
  });

  it('resolves the canonical Security route and rejects the retired account path', () => {
    expect(resolveDashboardRoute('/security')).toEqual({
      page: 'security',
      dataWidePanel: true,
      currentPath: '/security',
    });
    expect(resolveDashboardRoute('/settings/user-workspace')).toBeNull();
  });

  it('drops stale human-scope params when targeting Security', () => {
    expect(filterSettingsQueryForTarget('?scope=user&q=team&section=user-workspace', '/security'))
      .toBe('/security');
    expect(filterSettingsQueryForTarget('?scope=workspace', '/security'))
      .toBe('/security');
  });
});
